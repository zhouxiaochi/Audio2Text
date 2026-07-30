import type {
  AudioTask,
  BackendJob,
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
