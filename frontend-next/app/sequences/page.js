"use client";

import { useEffect, useState } from "react";
import api from "@/lib/api";
import { Users, Mail, Building2, Search, Clock, CheckCircle, AlertCircle, Briefcase, CalendarDays, Send, ChevronDown, ChevronUp } from "lucide-react";
import TopNav from "@/app/components/TopNav";

/* ── helpers ── */
function Avatar({ name, email }) {
  const initials = name
    ? name.split(" ").map(w => w[0]).slice(0, 2).join("").toUpperCase()
    : email?.[0]?.toUpperCase() || "?";
  const hue = [...(name || email || "")].reduce((a, c) => a + c.charCodeAt(0), 0) % 360;
  return (
    <div className="w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold shrink-0"
      style={{ background: `hsl(${hue},55%,22%)`, color: `hsl(${hue},70%,70%)`, border: `1.5px solid hsl(${hue},40%,35%)` }}>
      {initials}
    </div>
  );
}

// Format a UTC ISO string into local IST display
function fmtDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return d.toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    weekday: "short", day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function fmtShortDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return d.toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit",
  });
}

function timeUntil(iso) {
  if (!iso) return "";
  const diff = new Date(iso) - Date.now();
  if (diff <= 0) return "overdue";
  const h = Math.floor(diff / 3600000);
  const d = Math.floor(h / 24);
  if (d > 0) return `in ${d}d ${h % 24}h`;
  return `in ${h}h`;
}

function StatusBadge({ pending, sent, failed }) {
  if (sent > 0 && pending === 0) {
    return <span className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full"
      style={{ background: "rgba(46,170,101,0.1)", color: "#2eaa65", border: "1px solid rgba(46,170,101,0.25)" }}>
      <CheckCircle size={9} /> Done
    </span>;
  }
  if (pending > 0) {
    return <span className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full"
      style={{ background: "rgba(124,109,255,0.12)", color: "#7c6dff", border: "1px solid rgba(124,109,255,0.3)" }}>
      <Clock size={9} /> {pending} pending
    </span>;
  }
  if (failed > 0) {
    return <span className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full"
      style={{ background: "rgba(224,80,80,0.1)", color: "#e05050", border: "1px solid rgba(224,80,80,0.25)" }}>
      <AlertCircle size={9} /> {failed} failed
    </span>;
  }
  return <span className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full"
    style={{ background: "var(--neutral-soft)", color: "var(--text-subtle)", border: "1px solid var(--border)" }}>
    No emails
  </span>;
}

function EmailTimeline({ jobs, onReschedule }) {
  const [reschedulingId, setReschedulingId] = useState(null);
  const [newTime, setNewTime] = useState("");
  const [saving, setSaving] = useState(false);

  if (!jobs?.length) return null;

  const stepLabels = ["Initial Email", "Follow-up 1", "Follow-up 2"];
  const stepColors = ["#7c6dff", "#3b9eff", "#22d3ee"];

  const handleSave = async (job) => {
    setSaving(true);
    try {
      const dt = new Date(newTime).toISOString();
      await api.post(`/api/mail-jobs/${job.id}/reschedule`, { new_time: dt });
      if (onReschedule) onReschedule(job.id, dt);
      setReschedulingId(null);
    } catch (err) {
      alert("Failed to reschedule: " + (err.response?.data?.detail || err.message));
    }
    setSaving(false);
  };

  return (
    <div className="mt-3 flex flex-col gap-0">
      {jobs.map((job, i) => {
        const color = stepColors[i] || "#888";
        const isLast = i === jobs.length - 1;
        const isSent = job.status === "sent";
        const isPending = job.status === "pending" || job.status === "gmail_scheduled";
        const isFailed = job.status === "failed";
        const isBlocked = job.status === "blocked";
        const isCancelled = job.status === "cancelled";

        let dotColor = "#555";
        let dotGlow = "none";
        if (isSent) dotColor = "#2eaa65";
        else if (isPending) { dotColor = color; dotGlow = `0 0 6px ${color}80`; }
        else if (isFailed) dotColor = "#e05050";
        else if (isBlocked || isCancelled) dotColor = "#f5a623";

        return (
          <div key={job.id} className="flex gap-3">
            {/* timeline spine */}
            <div className="flex flex-col items-center shrink-0">
              <div className="w-2 h-2 rounded-full mt-1 shrink-0" style={{ background: dotColor, boxShadow: dotGlow }} />
              {!isLast && <div className="w-[1px] flex-1 my-1" style={{ background: "var(--border)" }} />}
            </div>
            {/* content */}
            <div className="pb-3 min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color }}>{stepLabels[i] || `Step ${i + 1}`}</span>
                {isSent && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: "rgba(46,170,101,0.1)", color: "#2eaa65" }}>Sent</span>}
                {isPending && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: `${color}18`, color }}>{timeUntil(job.scheduled_at)}</span>}
                {isFailed && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: "rgba(224,80,80,0.1)", color: "#e05050" }}>Failed</span>}
                {isBlocked && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: "rgba(245,166,35,0.1)", color: "#f5a623" }}>Blocked</span>}
                {isCancelled && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: "rgba(245,166,35,0.1)", color: "#f5a623" }}>Cancelled</span>}
                
                {isPending && reschedulingId !== job.id && (
                  <button onClick={() => {
                      setReschedulingId(job.id);
                      const d = new Date(job.scheduled_at);
                      // Format to YYYY-MM-DDTHH:mm for datetime-local input
                      const localIso = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
                      setNewTime(localIso);
                    }} 
                    className="text-[9px] text-[#7c6dff] hover:underline ml-auto">
                    Reschedule
                  </button>
                )}
              </div>
              
              {reschedulingId === job.id ? (
                <div className="mt-2 flex items-center gap-2 bg-[var(--neutral-soft)] p-2 rounded border border-border">
                  <input 
                    type="datetime-local" 
                    value={newTime}
                    onChange={e => setNewTime(e.target.value)}
                    className="text-xs bg-transparent border border-[var(--border)] rounded px-1.5 py-0.5 text-text-main"
                  />
                  <button onClick={() => handleSave(job)} disabled={saving} className="text-[10px] font-bold bg-[#7c6dff] text-white px-2 py-1 rounded">
                    {saving ? "..." : "Save"}
                  </button>
                  <button onClick={() => setReschedulingId(null)} className="text-[10px] text-text-subtle hover:text-text-main">
                    Cancel
                  </button>
                </div>
              ) : (
                <div className="text-xs text-text-muted font-mono mt-0.5">{fmtDate(job.scheduled_at)}</div>
              )}
              
              {job.subject && <div className="text-[11px] text-text-subtle mt-0.5 truncate max-w-[280px]">{job.subject}</div>}
              {isFailed && job.last_error && <div className="text-[11px] text-[#e05050] mt-1 italic max-w-[280px] truncate" title={job.last_error}>{job.last_error}</div>}
              {isBlocked && job.blocked_reason && <div className="text-[11px] text-[#f5a623] mt-1 italic max-w-[280px] truncate" title={job.blocked_reason}>Reason: {job.blocked_reason.replace(/_/g, ' ')}</div>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ContactCard({ c, onReplied }) {
  const [expanded, setExpanded] = useState(false);
  const [jobs, setJobs] = useState(null);
  const [loadingJobs, setLoadingJobs] = useState(false);
  const [markingReplied, setMarkingReplied] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [replied, setReplied] = useState(c.has_replied === 1);

  const handleExpand = async () => {
    if (!expanded && !jobs) {
      setLoadingJobs(true);
      try {
        const res = await api.get(`/api/contacts/${c.id}/jobs`);
        setJobs(res.data.jobs || []);
      } catch {
        setJobs([]);
      }
      setLoadingJobs(false);
    }
    setExpanded(e => !e);
  };

  const handleMarkReplied = async () => {
    setMarkingReplied(true);
    try {
      await api.post(`/api/contacts/${c.id}/mark-replied`);
      setReplied(true);
      if (jobs) setJobs(jobs.map(j => j.status === 'pending' && j.parent_job_id ? {...j, status: 'cancelled'} : j));
      if (onReplied) onReplied(c.id);
    } catch (err) {
      alert('Failed to mark replied: ' + err.message);
    }
    setMarkingReplied(false);
  };

  const handleCancelSequence = async () => {
    setCancelling(true);
    try {
      await api.post(`/api/contacts/${c.id}/cancel-jobs`);
      if (jobs) setJobs(jobs.map(j => j.status === 'pending' || j.status === 'gmail_scheduled' ? {...j, status: 'cancelled'} : j));
    } catch (err) {
      alert('Failed to cancel sequence: ' + err.message);
    }
    setCancelling(false);
  };

  const nextDate = c.next_scheduled_at;
  const sentCount = c.sent_count || 0;
  const pendingCount = c.pending_count || 0;
  const totalCount = c.total_jobs || 0;
  const hasActiveJobs = pendingCount > 0;

  return (
    <div className="border border-border rounded-xl overflow-hidden transition-all"
      style={{ background: "var(--surface)" }}>
      {/* Main row */}
      <div className="px-5 py-4 flex items-center gap-4">
        <Avatar name={c.name} email={c.email} />

        {/* Identity */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-sm text-text-main">{c.name || "—"}</span>
            {replied && <span className="text-[10px] font-bold px-2 py-0.5 rounded-full" style={{ background: "rgba(46,170,101,0.1)", color: "#2eaa65", border: "1px solid rgba(46,170,101,0.2)" }}>✓ Replied</span>}
            <StatusBadge pending={pendingCount} sent={sentCount} failed={c.failed_count || 0} />
          </div>
          <div className="text-xs text-text-muted font-mono mt-0.5">{c.email}</div>
          {c.title && <div className="text-[11px] text-text-subtle mt-0.5">{c.title} · {c.company}</div>}
        </div>

        {/* Stats */}
        <div className="hidden sm:flex items-center gap-5 shrink-0">
          {/* Progress */}
          <div className="text-center">
            <div className="text-base font-bold text-text-main">{sentCount}<span className="text-text-subtle font-normal text-xs">/{totalCount}</span></div>
            <div className="text-[9px] font-bold uppercase tracking-widest text-text-subtle mt-0.5">Sent</div>
          </div>

          {/* Next trigger */}
          {nextDate ? (
            <div className="text-right min-w-[90px]">
              <div className="text-xs font-bold text-[#7c6dff]">{fmtShortDate(nextDate)}</div>
              <div className="text-[9px] text-text-subtle mt-0.5 font-mono">{timeUntil(nextDate)}</div>
              <div className="text-[9px] font-bold uppercase tracking-widest text-text-subtle mt-0.5">Next email</div>
            </div>
          ) : sentCount > 0 ? (
            <div className="text-right min-w-[90px]">
              <div className="text-xs font-bold text-[#2eaa65]">Sequence done</div>
              <div className="text-[9px] font-bold uppercase tracking-widest text-text-subtle mt-0.5">
                {c.last_sent_at ? fmtShortDate(c.last_sent_at) : ""}
              </div>
            </div>
          ) : null}
        </div>

        {/* Expand toggle */}
        <button
          onClick={handleExpand}
          className="w-7 h-7 rounded-lg flex items-center justify-center text-text-subtle hover:text-text-main hover:bg-neutral-soft transition-all shrink-0"
        >
          {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
        </button>
      </div>

      {/* Mobile next date */}
      {nextDate && (
        <div className="sm:hidden px-5 pb-3 flex items-center gap-2">
          <CalendarDays size={12} className="text-[#7c6dff] shrink-0" />
          <span className="text-xs text-[#7c6dff] font-bold">Next: {fmtShortDate(nextDate)}</span>
          <span className="text-[10px] text-text-subtle font-mono">({timeUntil(nextDate)})</span>
        </div>
      )}

      {/* Expanded timeline */}
      {expanded && (
        <div className="border-t border-border px-5 py-4" style={{ background: "var(--surface-sunk)" }}>
          {loadingJobs ? (
            <div className="flex items-center gap-2 text-xs text-text-subtle">
              <div className="w-4 h-4 rounded-full border-2 border-[rgba(124,109,255,0.2)] border-t-[#7c6dff] animate-spin" />
              Loading schedule…
            </div>
          ) : jobs?.length ? (
            <>
              <div className="flex items-center justify-between gap-3 mb-3">
                <div className="text-[10px] font-bold uppercase tracking-widest text-text-subtle flex items-center gap-1.5">
                  <CalendarDays size={10} /> Email Schedule
                </div>
                {!replied && hasActiveJobs && (
                  <div className="flex items-center gap-2">
                    <button
                      onClick={handleCancelSequence}
                      disabled={cancelling}
                      className="text-[10px] font-bold px-2.5 py-1 rounded-full border transition-all"
                      style={{
                        background: cancelling ? "var(--neutral-soft)" : "rgba(245,166,35,0.1)",
                        borderColor: "rgba(245,166,35,0.3)",
                        color: "#f5a623",
                        cursor: cancelling ? "wait" : "pointer",
                      }}
                    >
                      {cancelling ? "Cancelling…" : "Cancel Sequence"}
                    </button>
                    <button
                      onClick={handleMarkReplied}
                      disabled={markingReplied}
                      className="text-[10px] font-bold px-2.5 py-1 rounded-full border transition-all"
                      style={{
                        background: markingReplied ? "var(--neutral-soft)" : "rgba(46,170,101,0.1)",
                        borderColor: "rgba(46,170,101,0.3)",
                        color: "#2eaa65",
                        cursor: markingReplied ? "wait" : "pointer",
                      }}
                    >
                      {markingReplied ? "Cancelling…" : "✓ They Replied — Cancel Follow-ups"}
                    </button>
                  </div>
                )}
              </div>
              <EmailTimeline 
                jobs={jobs} 
                onReschedule={(jobId, newTime) => {
                  setJobs(jobs.map(j => j.id === jobId ? { ...j, scheduled_at: newTime } : j));
                }} 
              />
            </>
          ) : (
            <div className="text-xs text-text-subtle">No emails scheduled.</div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ContactsPage() {
  const [contacts, setContacts] = useState([]);
  const [loading,  setLoading]  = useState(true);
  const [search,   setSearch]   = useState("");

  useEffect(() => {
    const fetchContacts = () => {
      api.get("/api/contacts")
        .then(res => { setContacts(res.data.contacts || []); setLoading(false); })
        .catch(() => setLoading(false));
    };
    fetchContacts();
    const interval = setInterval(fetchContacts, 5000);
    return () => clearInterval(interval);
  }, []);

  const filtered = contacts.filter(c =>
    (c.pending_count > 0 || c.gmail_scheduled_count > 0) && (
      !search ||
      c.name?.toLowerCase().includes(search.toLowerCase()) ||
      c.email?.toLowerCase().includes(search.toLowerCase()) ||
      c.company?.toLowerCase().includes(search.toLowerCase())
    )
  );

  const totalScheduled = contacts.reduce((a, c) => a + (c.pending_count || 0), 0);
  const totalSent      = contacts.reduce((a, c) => a + (c.sent_count || 0), 0);

  return (
    <div className="w-[min(1100px,calc(100vw-32px))] mx-auto pb-20">
      <TopNav />
      <main>
        {/* Header */}
        <div className="mb-8 flex items-end justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-3xl font-extrabold tracking-tight flex items-center gap-3"
              style={{ background: "linear-gradient(120deg, var(--text-main) 0%, #7c6dff 100%)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
              <Users size={26} className="text-[#7c6dff]" style={{ WebkitTextFillColor: "initial" }} />
              Active Sequences
            </h1>
            <p className="text-text-muted mt-2 text-sm">Contacts currently in an active email sequence.</p>
          </div>
          {!loading && contacts.length > 0 && (
            <div className="flex items-center gap-6 shrink-0">
              <div className="text-right">
                <div className="text-2xl font-bold text-text-main">{contacts.length}</div>
                <div className="text-[10px] font-bold uppercase tracking-widest text-text-subtle">Total</div>
              </div>
              <div className="text-right">
                <div className="text-2xl font-bold" style={{ color: "#7c6dff" }}>{totalScheduled}</div>
                <div className="text-[10px] font-bold uppercase tracking-widest text-text-subtle">Scheduled</div>
              </div>
              <div className="text-right">
                <div className="text-2xl font-bold" style={{ color: "#2eaa65" }}>{totalSent}</div>
                <div className="text-[10px] font-bold uppercase tracking-widest text-text-subtle">Sent</div>
              </div>
            </div>
          )}
        </div>

        {/* Search */}
        <div className="card overflow-hidden mb-4">
          <div className="card-glow" />
          <div className="p-4 flex items-center gap-3">
            <Search size={15} className="text-text-subtle shrink-0" />
            <input
              type="search"
              placeholder="Search by name, email or company…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="!border-0 !bg-transparent !shadow-none !p-0 !rounded-none text-sm placeholder:text-text-subtle"
            />
            {search && (
              <span className="text-xs text-text-subtle shrink-0">{filtered.length} result{filtered.length !== 1 ? "s" : ""}</span>
            )}
          </div>
        </div>

        {/* Content */}
        {loading ? (
          <div className="p-16 text-center text-text-muted flex flex-col items-center gap-3">
            <div className="w-7 h-7 rounded-full border-2 border-[rgba(124,109,255,0.2)] border-t-[#7c6dff] animate-spin" />
            Loading contacts…
          </div>
        ) : filtered.length === 0 ? (
          <div className="card p-16 text-center flex flex-col items-center gap-3">
            <div className="card-glow" />
            <div className="w-14 h-14 rounded-2xl bg-[rgba(124,109,255,0.08)] flex items-center justify-center text-[#7c6dff]">
              <Users size={28} />
            </div>
            <p className="text-text-muted text-sm">
              {search ? "No contacts match your search." : "No contacts yet. Send a campaign to add contacts!"}
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {filtered.map(c => <ContactCard key={c.id} c={c} onReplied={(id) => setContacts(cs => cs.map(x => x.id===id ? {...x, has_replied:1} : x))} />)}
          </div>
        )}
      </main>
    </div>
  );
}
