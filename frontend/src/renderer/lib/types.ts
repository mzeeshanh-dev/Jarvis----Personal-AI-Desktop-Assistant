export interface ToolRun {
  callId: string;
  tool: string;
  level?: number;
  arguments?: Record<string, unknown>;
  ok?: boolean;
  summary?: string;
  durationMs?: number;
  running: boolean;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  taskId?: string;
  status?: string;
  tools: ToolRun[];
  createdAt: number;
}

export interface ActiveTask {
  id: string;
  conversationId: string;
  text: string;
  source: string;
  phase: string;
  detail?: string;
  startedAt: number;
}

export interface Approval {
  id: string;
  task_id: string;
  tool: string;
  level: number;
  level_name: string;
  reason: string;
  summary: string;
  arguments: Record<string, unknown>;
  timeout_s: number;
  receivedAt: number;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  message_count: number;
}

export interface VoiceInfo {
  state: string;
  level: number;
  wakeWord: boolean;
  paused: boolean;
  transcript?: string;
  error?: string;
  hint?: string;
}

export interface Settings {
  general: { shortcut: string; launch_at_login: boolean; close_to_tray: boolean; minimize_to_tray: boolean; float_always_on_top: boolean; show_float_on_voice: boolean; user_name: string };
  voice: {
    input_device: number | null; wake_word_enabled: boolean; wake_word_threshold: number; vad_threshold: number; silence_ms: number;
    max_record_s: number; no_speech_timeout_s: number; noise_reduction: boolean; language: string; tts_engine: 'piper' | 'system' | 'off';
    piper_voice: string; speak_responses: boolean; speak_typed_responses: boolean; tts_rate: number;
  };
  providers: {
    quota_threshold: number; request_timeout_s: number; max_attempts: number; max_wait_s: number;
    reasoning_effort: 'low' | 'medium' | 'high';
    models: { simple: string; complex: string; summarization: string; transcription: string };
    local_limits: Record<string, { rpm: number | null; tpd: number | null; ash: number | null; asd: number | null }>;
  };
  permissions: {
    level3_policy: 'auto' | 'ask'; allowlisted_commands_auto: boolean; tool_overrides: Record<string, 'auto' | 'ask' | 'deny'>;
    workspace_roots: string[]; command_allowlist: string[]; approval_timeout_s: number;
  };
  browser: { channel: 'msedge' | 'chrome' | 'chromium'; headless: boolean; use_existing_chrome: boolean; cdp_url: string; search_engine: 'google' | 'bing' | 'duckduckgo' };
  automation: { max_iterations: number; tool_timeout_s: number; command_timeout_s: number; max_output_kb: number; max_concurrent_tasks: number; parallel_tools: boolean };
  performance: { history_messages: number; context_char_budget: number; tool_result_char_limit: number };
}

export type DeepPartial<T> = { [K in keyof T]?: T[K] extends object ? DeepPartial<T[K]> : T[K] };
