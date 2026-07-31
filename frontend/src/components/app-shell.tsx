"use client";

import { useEffect, useState } from "react";
import { AuthScreen } from "@/components/auth-screen";
import { AudioWorkspace } from "@/components/audio-workspace";
import { getCurrentAccount, logout } from "@/lib/api";
import type { Account } from "@/lib/types";

export function AppShell() {
  const [account, setAccount] = useState<Account | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getCurrentAccount().then(setAccount).catch(() => setAccount(null)).finally(() => setLoading(false));
  }, []);

  if (loading) return <main className="auth-page">正在验证登录状态…</main>;
  if (!account) return <AuthScreen onAuthenticated={setAccount} />;

  return (
    <>
      <div className="account-bar">
        <span>{account.username} · 余额 ${account.balance_usd.toFixed(2)}</span>
        {account.role === "admin" && <a href="/admin">管理后台</a>}
        <button className="text-button" onClick={() => logout().finally(() => setAccount(null))}>退出登录</button>
      </div>
      <AudioWorkspace account={account} />
    </>
  );
}
