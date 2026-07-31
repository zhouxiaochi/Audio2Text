"use client";

import { FormEvent, useState } from "react";
import { login, register } from "@/lib/api";
import type { Account } from "@/lib/types";

export function AuthScreen({ onAuthenticated }: { onAuthenticated: (account: Account) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const account = mode === "login" ? await login(username, password) : await register(username, password);
      if (mode === "register") {
        onAuthenticated(await login(username, password));
      } else {
        onAuthenticated(account);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败，请稍后重试。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-page">
      <form className="auth-card" onSubmit={submit}>
        <p className="eyebrow">声记 · AUDIO2TEXT</p>
        <h1>{mode === "login" ? "登录账户" : "创建账户"}</h1>
        <p>登录后可上传音频、查看任务，并使用账户额度。</p>
        <label>用户名<input value={username} onChange={(event) => setUsername(event.target.value)} required minLength={3} maxLength={32} /></label>
        <label>密码<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} required minLength={8} /></label>
        {error && <p className="form-error">{error}</p>}
        <button type="submit" disabled={submitting}>{submitting ? "处理中…" : mode === "login" ? "登录" : "注册并登录"}</button>
        <button className="text-button" type="button" onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "没有账户？立即注册" : "已有账户？返回登录"}
        </button>
      </form>
    </main>
  );
}
