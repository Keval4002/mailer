"use client";

import { useEffect, useState } from "react";
import { Mail, CheckCircle2, AlertCircle, LogOut, Loader2 } from "lucide-react";
import api from "@/lib/api";

export default function GmailConnect() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [disconnecting, setDisconnecting] = useState(false);

  const fetchStatus = async () => {
    try {
      const res = await api.get("/auth/gmail/status");
      setStatus(res.data);
    } catch (err) {
      console.error("Failed to fetch Gmail status", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchStatus(); }, []);

  const handleDisconnect = async (e) => {
    e.preventDefault();
    setDisconnecting(true);
    try {
      // Use axios with maxRedirects: 0 so we don't follow the 303 to backend root
      await api.post("/auth/gmail/disconnect", null, { maxRedirects: 0 });
    } catch {
      // 303 redirect throws with maxRedirects:0 — that's expected and fine
    }
    setStatus({ connected: false });
    setDisconnecting(false);
  };

  if (loading) {
    return (
      <div className="bg-surface border border-border rounded-xl p-6 shadow-sm flex items-center gap-3">
        <div className="animate-pulse w-4 h-4 bg-border rounded-full" />
        <span className="text-text-muted text-sm">Checking Gmail connection...</span>
      </div>
    );
  }

  const isConnected = status?.connected === true;
  const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return (
    <div
      className={`border rounded-xl p-6 shadow-sm flex items-center justify-between gap-4 transition-all ${
        isConnected
          ? "bg-success-soft border-success"
          : "bg-surface border-border hover:border-border-strong"
      }`}
    >
      <div className="flex items-center gap-4">
        <div
          className={`p-3 rounded-full ${
            isConnected
              ? "bg-success text-[var(--surface)]"
              : "bg-neutral-soft text-text-muted"
          }`}
        >
          <Mail size={24} />
        </div>
        <div>
          <h3 className="m-0 text-lg font-semibold tracking-tight text-text-main flex items-center gap-2">
            Gmail Connection
            {isConnected ? (
              <CheckCircle2 size={18} className="text-success" />
            ) : (
              <AlertCircle size={18} className="text-text-muted" />
            )}
          </h3>
          <p className="text-sm text-text-muted mt-1">
            {isConnected ? (
              <span>Connected as <b>{status.email}</b></span>
            ) : (
              "Connect your Gmail account to start scheduling emails."
            )}
          </p>
        </div>
      </div>

      <div>
        {isConnected ? (
          <form onSubmit={handleDisconnect}>
            <button
              type="submit"
              disabled={disconnecting}
              className="btn-ghost !text-danger hover:!bg-danger-soft gap-2"
            >
              {disconnecting ? <Loader2 size={16} className="animate-spin" /> : <LogOut size={16} />}
              Disconnect
            </button>
          </form>
        ) : (
          <a
            href={`${apiBase}/auth/gmail/start`}
            className="btn gap-2"
          >
            <Mail size={16} /> Connect Gmail
          </a>
        )}
      </div>
    </div>
  );
}