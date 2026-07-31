"use client";

import { FormEvent, useEffect, useState } from "react";
import { getAdminSummary, getAdminUsage, getAdminUsers, getCurrentAccount, topUpCredit } from "@/lib/api";
import type { UsageEvent } from "@/lib/api";
import type { Account, AdminSummary } from "@/lib/types";

const currency = (amount: number) => `$${amount.toFixed(4)}`;

export default function AdminPage() {
  const [summary, setSummary] = useState<AdminSummary | null>(null);
  const [users, setUsers] = useState<Account[]>([]);
  const [usage, setUsage] = useState<UsageEvent[]>([]);
  const [error, setError] = useState("");
  const [username, setUsername] = useState("");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");

  const refresh = async () => {
    try {
      const account = await getCurrentAccount();
      if (account.role !== "admin") throw new Error("需要管理员权限。");
      const [nextSummary, nextUsers, nextUsage] = await Promise.all([getAdminSummary(), getAdminUsers(), getAdminUsage()]);
      setSummary(nextSummary);
      setUsers(nextUsers.items);
      setUsage(nextUsage.items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取管理数据。");
    }
  };

  useEffect(() => { void refresh(); }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      await topUpCredit(username, Number(amount), note);
      setAmount("");
      setNote("");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "充值失败。");
    }
  }

  return (
    <main className="admin-page">
      <header className="site-header"><div className="brand">声记 · 管理后台</div><a href="/">返回工作台</a></header>
      <section className="admin-content">
        <h1>运营概览</h1>
        {error && <p className="form-error">{error}</p>}
        {summary && <div className="metrics">
          <article><span>用户</span><strong>{summary.users}</strong></article>
          <article><span>任务</span><strong>{summary.jobs}</strong></article>
          <article><span>Provider 实际成本</span><strong>{currency(summary.raw_cost_usd)}</strong></article>
          <article><span>5 倍计费额</span><strong>{currency(summary.billable_usd)}</strong></article>
          <article><span>待确认成本记录</span><strong>{summary.pending_cost_events}</strong></article>
        </div>}
        <div className="admin-grid">
          <section className="card"><h2>账户充值</h2><form onSubmit={submit} className="admin-form">
            <label>用户名<input value={username} onChange={(event) => setUsername(event.target.value)} required /></label>
            <label>额度（USD）<input type="number" min="0.01" step="0.01" value={amount} onChange={(event) => setAmount(event.target.value)} required /></label>
            <label>备注<input value={note} onChange={(event) => setNote(event.target.value)} /></label>
            <button type="submit">充值</button>
          </form></section>
          <section className="card"><h2>用户</h2><div className="user-table">
            {users.map((user) => <div key={user.id}><span>{user.username} {user.role === "admin" && "（管理员）"}</span><strong>${user.balance_usd.toFixed(2)}</strong></div>)}
          </div></section>
        </div>
        <section className="card usage-table"><h2>最近 API 用量</h2>
          {usage.map((event, index) => <div key={`${event.job_id}-${event.created_at}-${index}`}>
            <span>{event.model} · {event.endpoint}</span>
            <span>{event.cost_status === "pending" ? "成本待确认" : `${currency(event.raw_cost_usd ?? 0)} → ${currency(event.billable_usd ?? 0)}`}</span>
          </div>)}
          {!usage.length && <p>尚无 API 用量记录。</p>}
        </section>
      </section>
    </main>
  );
}
