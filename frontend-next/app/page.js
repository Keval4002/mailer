"use client";

import { useEffect, useState } from "react";
import TopNav from "@/app/components/TopNav";
import GmailConnect from "@/app/components/GmailConnect";
import { Mail, Clock, AlertCircle, Loader2, Activity, ChevronRight } from "lucide-react";
import api from "@/lib/api";

function StatCard({ icon: Icon, label, value, accent = false, danger = false, loading }) {
  return (
    <div
      className="card group p-6 flex flex-col gap-3"
      style={accent ? { borderColor: "rgba(124,109,255,0.3)" } : {}}
    >
      <div className="card-glow" />
      <div className="flex items-center justify-between">
        <span
          className="text-[10px] font-bold uppercase tracking-widest"
          style={{ color: accent ? "#7c6dff" : danger ? "var(--danger)" : "var(--text-subtle)" }}
        >
          {label}
        </span>
        <div
          className="w-8 h-8 rounded-lg flex items-center justify-center"
          style={{
            background: accent
              ? "rgba(124,109,255,0.12)"
              : danger
              ? "var(--danger-soft)"
              : "var(--neutral-soft)",
          }}
        >
          <Icon
            size={16}
            style={{ color: accent ? "#7c6dff" : danger ? "var(--danger)" : "var(--text-muted)" }}
          />
        </div>
      </div>
      <div
        className="text-4xl font-black tracking-tight"
        style={{ color: accent ? "#7c6dff" : danger ? "var(--danger)" : "var(--text-main)" }}
      >
        {loading ? <Loader2 size={28} className="animate-spin opacity-30" /> : value}
      </div>
      <div
        className="text-xs text-text-subtle mt-1 flex items-center gap-1 group-hover:gap-2 transition-all"
        style={{ color: "var(--text-subtle)" }}
      >
        <span>View details</span>
        <ChevronRight size={12} />
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const [stats, setStats]         = useState({ pending: 0, sentToday: 0, failed: 0 });
  const [statsLoading, setLoading] = useState(true);

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const res  = await api.get("/api/mail-jobs");
        const jobs = res.data.mail_jobs || [];
        const today = new Date().toDateString();
        setStats({
          pending:   jobs.filter(j => j.status === "pending").length,
          sentToday: jobs.filter(j => j.status === "sent" && new Date(j.sent_at).toDateString() === today).length,
          failed:    jobs.filter(j => j.status === "failed").length,
        });
      } catch (err) {
        console.error("Failed to fetch mail jobs", err);
      } finally {
        setLoading(false);
      }
    };
    fetchStats();
  }, []);

  return (
    <div className="w-[min(1100px,calc(100vw-32px))] mx-auto pb-20">
      <TopNav />
      <main>
        <div className="space-y-6">
          <GmailConnect />

          {/* Stats row */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
            <StatCard icon={Mail}         label="Pending"    value={stats.pending}   accent  loading={statsLoading} />
            <StatCard icon={Clock}        label="Sent Today" value={stats.sentToday}          loading={statsLoading} />
            <StatCard icon={AlertCircle}  label="Failed"     value={stats.failed}    danger  loading={statsLoading} />
          </div>

          {/* Active workflows */}
          <div className="card overflow-hidden">
            <div className="card-glow" />
            <div className="flex items-center gap-3 p-5 border-b border-border">
              <div className="w-8 h-8 rounded-lg bg-[rgba(124,109,255,0.1)] flex items-center justify-center text-[#7c6dff]">
                <Activity size={16} />
              </div>
              <h2 className="m-0 text-base font-bold tracking-tight text-text-main">Active Workflows</h2>
            </div>

            {statsLoading ? (
              <div className="p-12 text-center text-text-muted flex items-center justify-center gap-2">
                <Loader2 size={18} className="animate-spin" /> Loading…
              </div>
            ) : stats.pending === 0 && stats.sentToday === 0 ? (
              <div className="p-12 text-center text-text-muted text-sm">
                No active workflows. Head over to the Campaign Builder to schedule some emails.
              </div>
            ) : (
              <div className="p-5 flex flex-wrap gap-4 text-sm">
                <div className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-[rgba(124,109,255,0.08)] border border-[rgba(124,109,255,0.2)]">
                  <span className="w-2 h-2 rounded-full bg-[#7c6dff]" />
                  <span className="text-[#7c6dff] font-semibold">{stats.pending}</span>
                  <span className="text-text-muted">queued</span>
                </div>
                <div className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-success-soft border border-[rgba(46,170,101,0.2)]">
                  <span className="w-2 h-2 rounded-full bg-success" />
                  <span className="text-success font-semibold">{stats.sentToday}</span>
                  <span className="text-text-muted">sent today</span>
                </div>
                {stats.failed > 0 && (
                  <div className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-danger-soft border border-[rgba(224,80,80,0.2)]">
                    <span className="w-2 h-2 rounded-full bg-danger" />
                    <span className="text-danger font-semibold">{stats.failed}</span>
                    <span className="text-text-muted">failed</span>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}