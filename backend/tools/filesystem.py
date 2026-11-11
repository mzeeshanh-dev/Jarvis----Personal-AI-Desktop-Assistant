"""Filesystem tools, restricted to workspace roots (see tools.pathguard)."""
from __future__ import annotations

import asyncio
import fnmatch
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Optional

from pydantic import Field

from agent.tool_registry import ToolContext, ToolError, ToolInput, ToolRegistry, ToolResult
from tools import pathguard

SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".next",
             ".cache", "$recycle.bin", "appdata", ".idea", ".gradle", "site-packages"}
TEXT_EXT = {".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg",
            ".csv", ".log", ".html", ".css", ".scss", ".xml", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".rb",
            ".php", ".sql", ".sh", ".ps1", ".bat", ".env.example", ".vue", ".svelte", ".kt", ".swift", ".dart", ".r"}
MAX_READ_BYTES = 8 * 1024 * 1024


def _guard(ctx: ToolContext, raw: str, **kw) -> Path:
    return pathguard.resolve(raw, ctx.settings, ctx.services.paths, **kw)


# ------------------------------------------------------------------ inputs
class SearchFilesIn(ToolInput):
    query: str = Field(..., min_length=1, max_length=200, description="Words in the file name, or a glob like '*.pdf'")
    root: Optional[str] = Field(None, description="Folder to search; defaults to all workspace folders")
    extensions: Optional[list[str]] = Field(None, max_length=20, description="e.g. ['.pdf', '.docx']")
    search_contents: bool = Field(False, description="Also match text inside small text files")
    max_results: int = Field(30, ge=1, le=200)


class ListDirIn(ToolInput):
    path: str
    show_hidden: bool = False
    max_entries: int = Field(200, ge=1, le=1000)


class ReadFileIn(ToolInput):
    path: str
    max_chars: int = Field(20000, ge=100, le=200000)
    offset: int = Field(0, ge=0, description="Character offset to start reading from")


class CreateFileIn(ToolInput):
    path: str
    content: str = Field("", max_length=2_000_000)
    overwrite: bool = False


class EditFileIn(ToolInput):
    path: str
    find: Optional[str] = Field(None, description="Exact text to replace. Omit to append.")
    replace: str = Field("", max_length=2_000_000)
    replace_all: bool = False


class MoveCopyIn(ToolInput):
    source: str
    destination: str


class DeleteIn(ToolInput):
    path: str


# ---------------------------------------------------------------- helpers
def _walk(roots: list[Path], deadline: float, max_entries: int = 60000):
    seen = 0
    stack = list(roots)
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for entry in it:
                    seen += 1
                    if seen > max_entries or time.monotonic() > deadline:
                        return
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name.lower() not in SKIP_DIRS and not entry.name.startswith("."):
                                stack.append(Path(entry.path))
                        else:
                            yield entry
                    except OSError:
                        continue
        except OSError:
            continue


def _search_sync(args: SearchFilesIn, roots: list[Path]) -> tuple[list[dict], bool]:
    deadline = time.monotonic() + 15
    q = args.query.lower()
    is_glob = any(c in q for c in "*?[")
    words = q.split()
    exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in (args.extensions or [])}
    results: list[dict] = []
    for entry in _walk(roots, deadline):
        name = entry.name.lower()
        p = Path(entry.path)
        if exts and p.suffix.lower() not in exts:
            continue
        if pathguard.is_sensitive(p):
            continue
        hit = fnmatch.fnmatch(name, q) if is_glob else all(w in name for w in words)
        matched_in = "name"
        if not hit and args.search_contents and not is_glob and p.suffix.lower() in TEXT_EXT:
            try:
                if entry.stat().st_size < 1_000_000:
                    text = p.read_text(encoding="utf-8", errors="ignore").lower()
                    hit = all(w in text for w in words)
                    matched_in = "content"
            except OSError:
                pass
        if hit:
            st = entry.stat()
            results.append({"path": str(p), "name": entry.name, "size": st.st_size,
                            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)), "matched": matched_in})
            if len(results) >= args.max_results:
                return results, True
    results.sort(key=lambda r: r["modified"], reverse=True)
    return results, time.monotonic() > deadline


def _read_text(p: Path, max_chars: int, offset: int) -> tuple[str, int, str]:
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader  # lazy

        reader = PdfReader(str(p))
        parts = []
        total = 0
        for i, page in enumerate(reader.pages):
            t = page.extract_text() or ""
            parts.append(f"[page {i + 1}]\n{t}")
            total += len(t)
            if total > offset + max_chars:
                break
        text = "\n".join(parts)
        kind = f"pdf ({len(reader.pages)} pages)"
    elif suffix == ".docx":
        import re
        with zipfile.ZipFile(p) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
        xml = re.sub(r"</w:p>", "\n", xml)
        text = re.sub(r"<[^>]+>", "", xml)
        kind = "docx"
    else:
        if p.stat().st_size > MAX_READ_BYTES:
            raise ToolError(f"{p.name} is larger than {MAX_READ_BYTES // 1_000_000} MB.", code="too_large")
        raw = p.read_bytes()
        if b"\x00" in raw[:4096]:
            raise ToolError(f"{p.name} is a binary file.", code="binary")
        text = raw.decode("utf-8", errors="replace")
        kind = "text"
    return text[offset: offset + max_chars], len(text), kind


def _recycle(path: Path) -> None:
    """Move to the Windows Recycle Bin so deletes are recoverable."""
    if sys.platform != "win32":
        raise ToolError("Recycle Bin deletion is only supported on Windows.")
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    op = SHFILEOPSTRUCTW(None, FO_DELETE, str(path) + "\0", None,
                         FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI, False, None, None)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted:
        raise ToolError(f"Could not move {path.name} to the Recycle Bin (code {rc}).")


# ------------------------------------------------------------------ tools
def register(reg: ToolRegistry) -> None:
    @reg.tool("search_files", "Search for files by name (or glob) inside the user's workspace folders. Optionally search text contents.",
              input_model=SearchFilesIn, level=2, timeout_s=30, idempotent=True, category="filesystem")
    async def search_files(args: SearchFilesIn, ctx: ToolContext) -> ToolResult:
        roots = [_guard(ctx, args.root, must_exist=True)] if args.root else [r for r in pathguard.workspace_roots(ctx.settings) if r.exists()]
        results, partial = await asyncio.to_thread(_search_sync, args, roots)
        note = " (search stopped early; narrow it with root or extensions)" if partial and len(results) < args.max_results else ""
        return ToolResult.success(f"Found {len(results)} matching file(s){note}.", {"results": results, "searched": [str(r) for r in roots]})

    @reg.tool("list_directory", "List files and folders in a directory inside the workspace.",
              input_model=ListDirIn, level=2, idempotent=True, category="filesystem")
    async def list_directory(args: ListDirIn, ctx: ToolContext) -> ToolResult:
        p = _guard(ctx, args.path, must_exist=True)
        if not p.is_dir():
            raise ToolError(f"{p} is not a directory.", code="not_a_directory")

        def _list() -> list[dict]:
            out = []
            for e in sorted(os.scandir(p), key=lambda e: (not e.is_dir(), e.name.lower())):
                if (not args.show_hidden and e.name.startswith(".")) or pathguard.is_sensitive(Path(e.path)):
                    continue
                try:
                    st = e.stat()
                except OSError:
                    continue
                out.append({"name": e.name, "type": "dir" if e.is_dir() else "file", "size": None if e.is_dir() else st.st_size,
                            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))})
                if len(out) >= args.max_entries:
                    break
            return out
        entries = await asyncio.to_thread(_list)
        return ToolResult.success(f"{len(entries)} entries in {p}.", {"path": str(p), "entries": entries})

    @reg.tool("read_file", "Read a text, code, PDF or DOCX file from the workspace. Returns extracted text.",
              input_model=ReadFileIn, level=2, timeout_s=45, idempotent=True, category="filesystem")
    async def read_file(args: ReadFileIn, ctx: ToolContext) -> ToolResult:
        p = _guard(ctx, args.path, must_exist=True)
        if not p.is_file():
            raise ToolError(f"{p} is not a file.", code="not_a_file")
        text, total, kind = await asyncio.to_thread(_read_text, p, args.max_chars, args.offset)
        more = total > args.offset + len(text)
        return ToolResult.success(
            f"Read {len(text)} of {total} characters from {p.name} ({kind}).",
            {"path": str(p), "kind": kind, "total_chars": total, "offset": args.offset, "has_more": more, "text": text},
            untrusted=True, source=f"file:{p.name}")

    @reg.tool("create_file", "Create a new file in the workspace with the given content.",
              input_model=CreateFileIn, level=3, category="filesystem",
              describe=lambda a: f"Create file {a.path}" + (" (overwrite)" if a.overwrite else ""))
    async def create_file(args: CreateFileIn, ctx: ToolContext) -> ToolResult:
        p = _guard(ctx, args.path)
        if p.exists() and not args.overwrite:
            raise ToolError(f"{p} already exists. Set overwrite=true to replace it.", code="exists")

        def _write() -> None:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args.content, encoding="utf-8")
        await asyncio.to_thread(_write)
        return ToolResult.success(f"Created {p} ({len(args.content)} characters).", {"path": str(p)})

    @reg.tool("edit_file", "Edit a text file: replace exact text (find/replace) or append text when 'find' is omitted.",
              input_model=EditFileIn, level=3, category="filesystem",
              describe=lambda a: f"Edit {a.path}" + (f": replace “{a.find[:60]}”" if a.find else ": append text"))
    async def edit_file(args: EditFileIn, ctx: ToolContext) -> ToolResult:
        p = _guard(ctx, args.path, must_exist=True)

        def _edit() -> int:
            text = p.read_text(encoding="utf-8")
            if args.find is None:
                p.write_text(text + args.replace, encoding="utf-8")
                return 1
            count = text.count(args.find)
            if count == 0:
                raise ToolError("The text to replace was not found in the file.", code="no_match")
            if count > 1 and not args.replace_all:
                raise ToolError(f"The text occurs {count} times; set replace_all=true or include more context.", code="ambiguous")
            p.write_text(text.replace(args.find, args.replace) if args.replace_all else text.replace(args.find, args.replace, 1), encoding="utf-8")
            return count if args.replace_all else 1
        n = await asyncio.to_thread(_edit)
        return ToolResult.success(f"Edited {p.name} ({n} change{'s' if n != 1 else ''}).", {"path": str(p), "changes": n})

    @reg.tool("move_file", "Move or rename a file or folder within the workspace. Never overwrites.",
              input_model=MoveCopyIn, level=3, category="filesystem", describe=lambda a: f"Move {a.source} → {a.destination}")
    async def move_file(args: MoveCopyIn, ctx: ToolContext) -> ToolResult:
        src = _guard(ctx, args.source, must_exist=True, label="source")
        dst = _guard(ctx, args.destination, label="destination")
        if dst.is_dir():
            dst = dst / src.name
        if dst.exists():
            raise ToolError(f"{dst} already exists.", code="exists")
        await asyncio.to_thread(shutil.move, str(src), str(dst))
        return ToolResult.success(f"Moved {src.name} to {dst}.", {"source": str(src), "destination": str(dst)})

    @reg.tool("copy_file", "Copy a file or folder within the workspace. Never overwrites.",
              input_model=MoveCopyIn, level=3, category="filesystem", describe=lambda a: f"Copy {a.source} → {a.destination}")
    async def copy_file(args: MoveCopyIn, ctx: ToolContext) -> ToolResult:
        src = _guard(ctx, args.source, must_exist=True, label="source")
        dst = _guard(ctx, args.destination, label="destination")
        if dst.is_dir():
            dst = dst / src.name
        if dst.exists():
            raise ToolError(f"{dst} already exists.", code="exists")
        if src.is_dir():
            # Never copy credential files that sit inside the folder.
            def _ignore(d: str, names: list[str]) -> list[str]:
                return [n for n in names if pathguard.is_sensitive(Path(d) / n)]
            await asyncio.to_thread(shutil.copytree, src, dst, ignore=_ignore)
        else:
            await asyncio.to_thread(shutil.copy2, src, dst)
        return ToolResult.success(f"Copied {src.name} to {dst}.", {"source": str(src), "destination": str(dst)})

    @reg.tool("delete_file", "Delete a file or folder in the workspace by moving it to the Recycle Bin. Always asks the user.",
              input_model=DeleteIn, level=5, category="filesystem", describe=lambda a: f"Delete {a.path} (to Recycle Bin)")
    async def delete_file(args: DeleteIn, ctx: ToolContext) -> ToolResult:
        p = _guard(ctx, args.path, must_exist=True)
        if p in pathguard.workspace_roots(ctx.settings):
            raise ToolError("Refusing to delete a workspace root folder.", code="path_denied")
        await asyncio.to_thread(_recycle, p)
        return ToolResult.success(f"Moved {p.name} to the Recycle Bin.", {"path": str(p)})
