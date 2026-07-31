export const TASK_STATUSES = [
  "queued",
  "preprocessing",
  "transcribing",
  "translating",
  "rendering",
  "completed",
  "failed",
] as const;

export type TaskStatus = (typeof TASK_STATUSES)[number];

export interface AudioTask {
  id: string;
  status: TaskStatus;
  stage?: string;
  progress?: number;
  stage_progress?: number;
  filename?: string;
  markdown?: string;
  result?: {
    markdown?: string;
    text?: string;
    docx_url?: string;
    [key: string]: unknown;
  };
  docx_url?: string;
  error?: string | { message?: string; detail?: string };
  message?: string;
  created_at?: string;
  updated_at?: string;
  [key: string]: unknown;
}

export interface CreateTaskOptions {
  translate?: boolean;
  targetLanguage?: string;
}

export interface SaveMarkdownPayload {
  markdown: string;
}

export interface Account {
  id: string;
  username: string;
  role: "user" | "admin";
  balance_usd: number;
  created_at: string;
}

export interface AdminSummary {
  users: number;
  jobs: number;
  raw_cost_usd: number;
  billable_usd: number;
  pending_cost_events: number;
}

export interface BackendJob {
  id: string;
  original_filename?: string;
  status: "queued" | "processing" | "completed" | "failed";
  stage?: string;
  progress?: number;
  error?: string | null;
  created_at?: string;
  updated_at?: string;
  [key: string]: unknown;
}
