"use client";

import { useEffect, useState } from "react";
import TopNav from "@/app/components/TopNav";
import GmailConnect from "@/app/components/GmailConnect";
import { Mail, Clock, AlertCircle, Loader2 } from "lucide-react";
import api from "@/lib/api";

function StatCard({ icon: Icon, label, value, colorClass = "text-text-muted", loading }) {
  return (
    <div className="bg-surface border border-border rounded-xl p-6 shadow-sm">
      <div className={`flex items-center gap-3 ${colorClass} mb-2`}>
        <Icon size={18} />
        <span className="text-sm font-semibold uppercase tracking-wider">{label}</span>
      </div>
      <div className={`text-3xl font-bold ${colorClass}`}>
        {loading ? <Loader2 size={24} className="animate-spin opacity-40" /> : value}
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const [stats, setStats] = useState({ pending: 0, sentToday: 0, failed: 0 });
  const [statsLoading, setStatsLoading] = useState(true);

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const res = await api.get("/api/mail-jobs");
        const jobs = res.data.mail_jobs || [];
        const today = new Date().toDateString();
        setStats({
          pending: jobs.filter(j => j.status === "pending").length,
          sentToday: jobs.filter(j => j.status === "sent" && new Date(j.sent_at).toDateString() === today).length,
          failed: jobs.filter(j => j.status === "failed").length,
        });
      } catch (err) {
        console.error("Failed to fetch mail jobs", err);
      } finally {
        setStatsLoading(false);
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

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <StatCard icon={Mail} label="Pending" value={stats.pending} loading={statsLoading} />
            <StatCard icon={Clock} label="Sent Today" value={stats.sentToday} loading={statsLoading} />
            <StatCard icon={AlertCircle} label="Failed" value={stats.failed} colorClass="text-danger" loading={statsLoading} />
          </div>

          <div className="bg-surface border border-border rounded-xl shadow-sm overflow-hidden">
            <div className="p-6 border-b border-border">
              <h2 className="m-0 text-lg font-semibold tracking-tight">Active Workflows</h2>
            </div>
            {statsLoading ? (
              <div className="p-12 text-center text-text-muted flex items-center justify-center gap-2">
                <Loader2 size={18} className="animate-spin" /> Loading...
              </div>
            ) : stats.pending === 0 && stats.sentToday === 0 ? (
              <div className="p-12 text-center text-text-muted">
                No active workflows. Head over to the Campaign Builder to schedule some emails.
              </div>
            ) : (
              <div className="p-6 text-text-muted text-sm">
                {stats.pending} email{stats.pending !== 1 ? "s" : ""} queued · {stats.sentToday} sent today · {stats.failed} failed
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}