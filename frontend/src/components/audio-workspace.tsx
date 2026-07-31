"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  createTask,
  downloadArtifact,
  getTask,
  regenerateDocx,
  retryTask,
  saveMarkdown,
} from "@/lib/api";
import {
  FILE_ACCEPT,
  MAX_FILE_SIZE,
  formatBytes,
  validateAudioFile,
} from "@/lib/file";
import type { Account, AudioTask, TaskStatus } from "@/lib/types";
import {
  CheckIcon,
  DownloadIcon,
  ErrorIcon,
  FileAudioIcon,
  RefreshIcon,
  SaveIcon,
  UploadIcon,
  WaveIcon,
} from "@/components/icons";

const ACTIVE_STATUSES: TaskStatus[] = [
  "queued",
  "preprocessing",
  "transcribing",
  "translating",
  "rendering",
];

const STAGES: Array<{ status: TaskStatus; label: string; detail: string }> = [
  { status: "queued", label: "排队", detail: "等待处理资源" },
  { status: "preprocessing", label: "预处理", detail: "读取并优化音频" },
  { status: "transcribing", label: "转写", detail: "识别语音内容" },
  { status: "translating", label: "翻译", detail: "生成目标语言文本" },
  { status: "rendering", label: "渲染", detail: "整理 Markdown 与文档" },
  { status: "completed", label: "完成", detail: "结果已准备就绪" },
];

function taskMarkdown(task: AudioTask | null): string {
  if (!task) return "";
  return task.markdown ?? task.result?.markdown ?? task.result?.text ?? "";
}

function taskError(task: AudioTask | null): string {
  if (!task?.error) return task?.message ?? "任务处理失败，请重试。";
  if (typeof task.error === "string") return task.error;
  return task.error.message ?? task.error.detail ?? "任务处理失败，请重试。";
}

function taskProgress(task: AudioTask): number {
  const explicit = task.progress ?? task.stage_progress;
  if (typeof explicit === "number") {
    return Math.max(0, Math.min(100, explicit <= 1 ? explicit * 100 : explicit));
  }
  const index = STAGES.findIndex(({ status }) => status === task.status);
  return task.status === "failed" ? 0 : Math.max(4, (index / (STAGES.length - 1)) * 100);
}

function currentStageIndex(status: TaskStatus): number {
  if (status === "failed") return -1;
  return STAGES.findIndex((stage) => stage.status === status);
}

function suggestedName(task: AudioTask, extension: string): string {
  const source = task.filename ?? "audio-transcript";
  return `${source.replace(/\.[^.]+$/, "")}.${extension}`;
}

export function AudioWorkspace({ account }: { account: Account }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [task, setTask] = useState<AudioTask | null>(null);
  const [markdown, setMarkdown] = useState("");
  const [view, setView] = useState<"edit" | "preview">("edit");
  const [dragging, setDragging] = useState(false);
  const translate = true;
  const [targetLanguage, setTargetLanguage] = useState("zh-CN");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);

  const applyTask = useCallback((nextTask: AudioTask) => {
    setTask(nextTask);
    const nextMarkdown = taskMarkdown(nextTask);
    if (nextMarkdown) setMarkdown(nextMarkdown);
  }, []);

  useEffect(() => {
    if (!task || !ACTIVE_STATUSES.includes(task.status)) return;
    let cancelled = false;

    const poll = async () => {
      try {
        const nextTask = await getTask(task.id);
        if (!cancelled) {
          applyTask(nextTask);
          if (ACTIVE_STATUSES.includes(nextTask.status)) {
            pollTimer.current = setTimeout(poll, 1800);
          }
        }
      } catch (error) {
        if (!cancelled) {
          const message = error instanceof Error ? error.message : "获取任务状态失败。";
          setLocalError(message);
          if (!/任务不存在|已过期/.test(message)) {
            pollTimer.current = setTimeout(poll, 4000);
          }
        }
      }
    };

    pollTimer.current = setTimeout(poll, 900);
    return () => {
      cancelled = true;
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [applyTask, task?.id, task?.status]);

  const chooseFile = (nextFile: File | undefined) => {
    if (!nextFile) return;
    const error = validateAudioFile(nextFile);
    if (error) {
      setLocalError(error);
      return;
    }
    setFile(nextFile);
    setTask(null);
    setMarkdown("");
    setLocalError(null);
    setNotice(null);
  };

  const handleCreate = async () => {
    if (!file) {
      setLocalError("请先选择一个音频文件。");
      return;
    }
    setBusy("create");
    setLocalError(null);
    setNotice(null);
    try {
      applyTask(await createTask(file, { translate, targetLanguage }));
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : "创建任务失败。");
    } finally {
      setBusy(null);
    }
  };

  const handleRetry = async () => {
    if (!task) return;
    setBusy("retry");
    setLocalError(null);
    try {
      applyTask(await retryTask(task.id));
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : "重试任务失败。");
    } finally {
      setBusy(null);
    }
  };

  const handleSave = async () => {
    if (!task) return;
    setBusy("save");
    setLocalError(null);
    try {
      const updated = await saveMarkdown(task.id, { markdown });
      setTask(updated);
      setNotice("Markdown 已保存");
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : "保存失败。");
    } finally {
      setBusy(null);
    }
  };

  const handleRegenerate = async () => {
    if (!task) return;
    setBusy("docx");
    setLocalError(null);
    try {
      await saveMarkdown(task.id, { markdown });
      applyTask(await regenerateDocx(task.id));
      setNotice("DOCX 已重新生成");
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : "生成 DOCX 失败。");
    } finally {
      setBusy(null);
    }
  };

  const handleDownload = async (format: "markdown" | "json" | "docx") => {
    if (!task) return;
    setBusy(`download-${format}`);
    setLocalError(null);
    try {
      let blob: Blob;
      if (format === "markdown") {
        blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
      } else if (format === "json") {
        blob = new Blob(
          [JSON.stringify({ ...task, markdown }, null, 2)],
          { type: "application/json;charset=utf-8" },
        );
      } else {
        blob = await downloadArtifact(task.id, format);
      }
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = suggestedName(task, format === "markdown" ? "md" : format);
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : "下载失败。");
    } finally {
      setBusy(null);
    }
  };

  const active = task && ACTIVE_STATUSES.includes(task.status);
  const completed = task?.status === "completed";
  const failed = task?.status === "failed";
  const stageIndex = task ? currentStageIndex(task.status) : -1;

  return (
    <main>
      <header className="site-header">
        <div className="brand">
          <span className="brand-mark"><WaveIcon /></span>
          <span>声记</span>
        </div>
        <div className="status-chip">
          <span className="status-dot" />
          {account.username} · 余额 ${account.balance_usd.toFixed(2)}
        </div>
      </header>

      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />

      <section className="hero">
        <p className="eyebrow">AUDIO TO DOCUMENT</p>
        <h1>让每段声音，都成为<br /><span>清晰的文字。</span></h1>
        <p className="hero-copy">上传录音，自动完成转写、翻译与文档整理。<br className="desktop-break" />处理费用按 OpenRouter 实际成本的 5 倍结算。</p>
        <p className="deployment-warning">
          演示环境使用临时存储：部署或服务重启会清除任务，请完成后立即下载产物。
        </p>
      </section>

      <section className="workspace">
        <div className="card upload-card">
          <div className="section-heading">
            <div>
              <span className="step-number">01</span>
              <h2>选择音频</h2>
            </div>
            <span className="section-hint">支持单个文件</span>
          </div>

          <button
            className={`dropzone ${dragging ? "is-dragging" : ""} ${file ? "has-file" : ""}`}
            onClick={() => inputRef.current?.click()}
            onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={(event) => { event.preventDefault(); setDragging(false); }}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              chooseFile(event.dataTransfer.files[0]);
            }}
            type="button"
          >
            <input
              ref={inputRef}
              accept={FILE_ACCEPT}
              hidden
              onChange={(event) => chooseFile(event.target.files?.[0])}
              type="file"
            />
            {file ? (
              <>
                <span className="drop-icon selected"><FileAudioIcon /></span>
                <strong>{file.name}</strong>
                <span>{formatBytes(file.size)} · 点击更换文件</span>
              </>
            ) : (
              <>
                <span className="drop-icon"><UploadIcon /></span>
                <strong>将音频拖到这里</strong>
                <span>或者 <em>浏览文件</em> 进行选择</span>
              </>
            )}
          </button>

          <div className="file-meta">
            <span>MP3 · WAV · M4A · MP4 · WEBM · FLAC</span>
            <span>最大 {formatBytes(MAX_FILE_SIZE)}</span>
          </div>

          <div className="options-row">
            <label className="switch-label">
              <input checked={translate} disabled type="checkbox" />
              <span className="switch" />
              生成中英对照
            </label>
            {translate && (
              <label className="language-select">
                目标语言
                <select value={targetLanguage} onChange={(event) => setTargetLanguage(event.target.value)}>
                  <option value="zh-CN">简体中文</option>
                </select>
              </label>
            )}
          </div>

          <button className="primary-button" disabled={!file || Boolean(active) || busy === "create"} onClick={handleCreate} type="button">
            {busy === "create" ? "正在上传…" : "开始转写"}
            <span>→</span>
          </button>
        </div>

        <div className="card progress-card">
          <div className="section-heading">
            <div>
              <span className="step-number">02</span>
              <h2>处理进度</h2>
            </div>
            {task && <span className={`task-badge ${task.status}`}>{task.status === "completed" ? "已完成" : task.status === "failed" ? "失败" : "处理中"}</span>}
          </div>

          {!task ? (
            <div className="empty-progress">
              <span><WaveIcon /></span>
              <p>任务创建后，处理进度会显示在这里</p>
            </div>
          ) : failed ? (
            <div className="failed-state">
              <span><ErrorIcon /></span>
              <h3>处理未能完成</h3>
              <p>{taskError(task)}</p>
              <button className="secondary-button" disabled={busy === "retry"} onClick={handleRetry} type="button">
                <RefreshIcon /> {busy === "retry" ? "正在重试…" : "重试任务"}
              </button>
            </div>
          ) : (
            <>
              <div className="progress-summary">
                <div>
                  <strong>{Math.round(taskProgress(task))}%</strong>
                  <span>{STAGES[Math.max(0, stageIndex)]?.detail}</span>
                </div>
                <div className="progress-track"><span style={{ width: `${taskProgress(task)}%` }} /></div>
              </div>
              <ol className="stage-list">
                {STAGES.map((stage, index) => {
                  const isDone = index < stageIndex || completed;
                  const isCurrent = index === stageIndex && !completed;
                  return (
                    <li className={`${isDone ? "done" : ""} ${isCurrent ? "current" : ""}`} key={stage.status}>
                      <span className="stage-marker">{isDone ? <CheckIcon /> : index + 1}</span>
                      <div><strong>{stage.label}</strong><small>{stage.detail}</small></div>
                    </li>
                  );
                })}
              </ol>
            </>
          )}
        </div>

        {(completed || markdown) && (
          <div className="card result-card">
            <div className="section-heading result-heading">
              <div>
                <span className="step-number">03</span>
                <h2>转写结果</h2>
              </div>
              <div className="view-tabs" role="tablist" aria-label="结果视图">
                <button aria-selected={view === "edit"} onClick={() => setView("edit")} role="tab" type="button">编辑</button>
                <button aria-selected={view === "preview"} onClick={() => setView("preview")} role="tab" type="button">预览</button>
              </div>
            </div>

            {view === "edit" ? (
              <textarea
                aria-label="Markdown 转写结果"
                className="markdown-editor"
                onChange={(event) => { setMarkdown(event.target.value); setNotice(null); }}
                spellCheck={false}
                value={markdown}
              />
            ) : (
              <article className="markdown-preview">
                <ReactMarkdown>{markdown || "*暂无内容*"}</ReactMarkdown>
              </article>
            )}

            <div className="result-actions">
              <div>
                <button className="secondary-button" disabled={busy === "save"} onClick={handleSave} type="button"><SaveIcon />{busy === "save" ? "保存中…" : "保存"}</button>
                <button className="secondary-button" disabled={busy === "docx"} onClick={handleRegenerate} type="button"><RefreshIcon />{busy === "docx" ? "生成中…" : "重新生成 DOCX"}</button>
              </div>
              <div className="download-group">
                {(["markdown", "json", "docx"] as const).map((format) => (
                  <button disabled={busy === `download-${format}`} key={format} onClick={() => handleDownload(format)} type="button">
                    <DownloadIcon />{format === "markdown" ? "Markdown" : format.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {(localError || notice) && (
          <div className={`toast ${localError ? "error" : "success"}`} role="status">
            {localError ? <ErrorIcon /> : <CheckIcon />}
            <span>{localError ?? notice}</span>
            <button aria-label="关闭提示" onClick={() => { setLocalError(null); setNotice(null); }} type="button">×</button>
          </div>
        )}
      </section>

      <footer>
        <span><WaveIcon /> 声记 Audio2Text</span>
        <span>音频只发送至你配置的本地后端</span>
      </footer>
    </main>
  );
}
