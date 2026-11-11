# PyInstaller spec for the Jarvis backend (onedir: fast startup, no temp extraction).
# Build with: npm run build:backend
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hidden = (
    collect_submodules("uvicorn")
    + collect_submodules("keyring.backends")
    + collect_submodules("api") + collect_submodules("agent") + collect_submodules("tools")
    + collect_submodules("providers") + collect_submodules("voice") + collect_submodules("integrations")
    + collect_submodules("core") + collect_submodules("storage")
    + ["win32ctypes.core", "pypdf"]
)
datas = collect_data_files("piper")  # espeak-ng-data used by Piper phonemizer

a = Analysis(
    ["main.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "PIL", "scipy", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="jarvis-backend",
    console=True,  # stdio is piped to Electron; spawned with windowsHide so no window appears
    icon="../resources/icons/icon.ico",
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="jarvis-backend")
