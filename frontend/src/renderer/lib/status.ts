import type { OrbState } from '../components/VoiceOrb';
import type { State } from './reducer';

export function orbState(s: State): OrbState {
  if (!s.connected) return 'offline';
  if (s.voice.state === 'listening') return 'listening';
  if (s.voice.state === 'transcribing') return 'transcribing';
  if (s.approvals.length) return 'awaiting';
  const tasks = Object.values(s.tasks);
  if (tasks.some((t) => t.phase === 'executing')) return 'executing';
  if (tasks.length) return 'thinking';
  if (s.playback.status === 'playing') return 'speaking';
  if (s.voice.state === 'error') return 'error';
  return 'idle';
}

const TOOL_LABELS: Record<string, string> = {
  open_application: 'Open app', close_application: 'Close app', list_running_applications: 'Running apps', open_url: 'Open URL',
  browser_open: 'Browser', browser_search: 'Web search', browser_navigate: 'Navigate', browser_read_page: 'Read page',
  browser_extract_content: 'Extract', browser_click: 'Click', browser_fill: 'Fill field', browser_screenshot: 'Screenshot',
  browser_close: 'Close browser', search_files: 'Search files', list_directory: 'List folder', read_file: 'Read file',
  create_file: 'Create file', edit_file: 'Edit file', move_file: 'Move', copy_file: 'Copy', delete_file: 'Delete',
  run_command: 'Run command', inspect_process: 'Inspect process', get_process_output: 'Process output', stop_process: 'Stop process',
  run_project: 'Run project', search_emails: 'Search Gmail', read_email: 'Read email', read_thread: 'Read thread',
  summarize_emails: 'Summarize emails', create_email_draft: 'Draft email',
};

export const toolLabel = (name: string) => TOOL_LABELS[name] ?? name.replace(/_/g, ' ');

export function phaseLabel(phase: string, detail?: string): string {
  switch (phase) {
    case 'thinking': return 'Thinking';
    case 'executing': return detail ? `Running ${detail.split(', ').map(toolLabel).join(', ')}` : 'Working';
    case 'awaiting_approval': return `Waiting for approval${detail ? ` · ${toolLabel(detail)}` : ''}`;
    case 'starting': case 'running': return 'Starting';
    default: return phase;
  }
}

export function timeAgo(ts: number): string {
  const s = Math.round((Date.now() - ts) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function duration(s: number | null | undefined): string {
  if (s == null) return '—';
  const v = Math.max(0, Math.round(s));
  if (v < 60) return `${v}s`;
  if (v < 3600) return `${Math.floor(v / 60)}m ${v % 60}s`;
  return `${Math.floor(v / 3600)}h ${Math.floor((v % 3600) / 60)}m`;
}
