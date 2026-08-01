import type {
  AudioTask,
  BackendJob,
  Account,
  AdminSummary,
  CreateTaskOptions,
  SaveMarkdownPayload,
  TaskStatus,
} from "@/lib/types";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ??
  (process.env.NODE_ENV === "production" ? "/api" : "http://localhost:8000");

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function frontendStatus(job: BackendJob): TaskStatus {
  if (job.status !== "processing") return job.status;
  const stage = job.stage ?? "";
  if (["probing", "normalizing", "chunking", "merging"].includes(stage)) {
    return "preprocessing";
  }
  if (stage === "transcribing") return "transcribing";
  if (["speakers", "translating"].includes(stage)) return "translating";
  if (stage === "rendering") return "rendering";
  return "queued";
}

async function withMarkdown(job: BackendJob): Promise<AudioTask> {
  let markdown: string | undefined;
  if (job.status === "completed") {
    try {
      markdown = await request<string>(
        `/jobs/${encodeURIComponent(job.id)}/markdown`,
        undefined,
        "text",
      );
    } catch {
      // A completed response can briefly precede artifact visibility.
    }
  }
  return {
    ...job,
    filename: job.original_filename,
    status: frontendStatus(job),
    markdown,
    error: job.error ?? undefined,
  };
}

async function request<T>(
  path: string,
  init?: RequestInit,
  responseType: "json" | "blob" | "text" = "json",
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      credentials: "include",
      headers: {
        ...(init?.body instanceof FormData
          ? {}
          : { "Content-Type": "application/json" }),
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError(
      `无法连接后端服务（${API_URL}），请确认服务已启动并允许跨域访问。`,
    );
  }

  if (!response.ok) {
    const body = await response.text();
    let message = body || `请求失败（HTTP ${response.status}）`;
    try {
      const parsed = JSON.parse(body) as {
        detail?: string;
        message?: string;
        error?: string;
      };
      message = parsed.detail ?? parsed.message ?? parsed.error ?? message;
    } catch {
      // Preserve a plain-text backend error.
    }
    throw new ApiError(message, response.status);
  }

  if (responseType === "blob") return (await response.blob()) as T;
  if (responseType === "text") return (await response.text()) as T;
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function register(username: string, password: string): Promise<Account> {
  return request<Account>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function login(username: string, password: string): Promise<Account> {
  return request<Account>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function logout(): Promise<void> {
  return request<void>("/auth/logout", { method: "POST" });
}

export function getCurrentAccount(): Promise<Account> {
  return request<Account>("/auth/me");
}

export function getAdminSummary(): Promise<AdminSummary> {
  return request<AdminSummary>("/admin/summary");
}

export function getAdminUsers(): Promise<{ items: Account[]; total: number }> {
  return request<{ items: Account[]; total: number }>("/admin/users");
}

export interface UsageEvent {
  user_id: string;
  job_id: string;
  endpoint: string;
  model: string;
  usage: Record<string, unknown>;
  raw_cost_usd: number | null;
  billable_usd: number | null;
  cost_status: "settled" | "pending";
  created_at: string;
}

export function getAdminUsage(): Promise<{ items: UsageEvent[]; total: number }> {
  return request<{ items: UsageEvent[]; total: number }>("/admin/usage");
}

export function topUpCredit(
  username: string,
  amount_usd: number,
  note: string,
): Promise<Account> {
  return request<Account>("/admin/credits", {
    method: "POST",
    body: JSON.stringify({
      username,
      amount_usd,
      note,
      idempotency_key: crypto.randomUUID(),
    }),
  });
}

export async function createTask(
  file: File,
  options: CreateTaskOptions,
): Promise<AudioTask> {
  void options;
  const formData = new FormData();
  formData.append("file", file);
  const job = await request<BackendJob>("/jobs", {
    method: "POST",
    body: formData,
  });
  return withMarkdown(job);
}

export async function getTask(taskId: string): Promise<AudioTask> {
  const job = await request<BackendJob>(`/jobs/${encodeURIComponent(taskId)}`);
  return withMarkdown(job);
}

export async function retryTask(taskId: string): Promise<AudioTask> {
  const job = await request<BackendJob>(`/jobs/${encodeURIComponent(taskId)}/retry`, {
    method: "POST",
  });
  return withMarkdown(job);
}

export function deleteTask(taskId: string): Promise<void> {
  return request<{ message: string }>(`/jobs/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
  }).then(() => undefined);
}

export function saveMarkdown(
  taskId: string,
  payload: SaveMarkdownPayload,
): Promise<AudioTask> {
  return request<{ message: string }>(`/jobs/${encodeURIComponent(taskId)}/markdown`, {
    method: "PUT",
    body: JSON.stringify(payload),
  }).then(() => getTask(taskId));
}

export function regenerateDocx(taskId: string): Promise<AudioTask> {
  return request<{ message: string }>(`/jobs/${encodeURIComponent(taskId)}/docx`, {
    method: "POST",
  }).then(() => getTask(taskId));
}

export function downloadArtifact(
  taskId: string,
  format: "markdown" | "json" | "docx",
): Promise<Blob> {
  return request<Blob>(
    `/jobs/${encodeURIComponent(taskId)}/download/${format === "markdown" ? "md" : format}`,
    undefined,
    "blob",
  );
}
