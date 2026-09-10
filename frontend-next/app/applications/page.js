"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import api from "@/lib/api";
import {
  Briefcase, ExternalLink, Plus, Calendar, Pencil, Trash2, X,
  Loader2, CheckCircle2, AlertCircle, Save, Search, SlidersHorizontal,
  List, LayoutGrid, ChevronLeft, ChevronRight, ArrowUpDown, Filter,
} from "lucide-react";
import TopNav from "@/app/components/TopNav";
import Link from "next/link";

/* ─────────────────── Constants ─────────────────── */
const ALL_STATUSES = [
  "Applied", "Emailed", "Got a reply", "Interview scheduled", "Rejected", "Offer",
];

const STATUS_STYLE = {
  "Applied":             "status-applied",
  "Emailed":             "status-emailed",
  "Got a reply":         "status-reply",
  "Interview scheduled": "status-interview",
  "Rejected":            "status-rejected",
  "Offer":               "status-offer",
};

const STATUS_DOT = {
  "Applied":             "#6b7280",
  "Emailed":             "#7c6dff",
  "Got a reply":         "#3b82f6",
  "Interview scheduled": "#f59e0b",
  "Rejected":            "#ef4444",
  "Offer":               "#22c55e",
};

const DATE_PRESETS = [
  { label: "All time", value: "all" },
  { label: "Today",    value: "today" },
  { label: "This week", value: "week" },
  { label: "This month", value: "month" },
];

const PAGE_SIZE = 20;

function getDateRange(preset) {
  const now = new Date();
  if (preset === "today") {
    const start = new Date(now); start.setHours(0, 0, 0, 0);
    return { date_from: start.toISOString(), date_to: null };
  }
  if (preset === "week") {
    const start = new Date(now); start.setDate(now.getDate() - now.getDay()); start.setHours(0, 0, 0, 0);
    return { date_from: start.toISOString(), date_to: null };
  }
  if (preset === "month") {
    const start = new Date(now.getFullYear(), now.getMonth(), 1);
    return { date_from: start.toISOString(), date_to: null };
  }
  return { date_from: null, date_to: null };
}

/* ─────────────────── Edit Modal ─────────────────── */
function EditModal({ app, onClose, onSaved }) {
  const [form, setForm] = useState({
    company_name: app.company_name || "",
    role:         app.role || "",
    job_url:      app.job_url || "",
    job_board:    app.job_board || "",
    status:       app.status || "Applied",
    notes:        app.notes || "",
  });
  const [saving, setSaving] = useState(false);
  const [error, setError]   = useState(null);

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const handleSave = async (e) => {
    e.preventDefault();
    setSaving(true); setError(null);
    try {
      await api.patch(`/api/applications/${app.id}`, form);
      onSaved(); onClose();
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to save.");
    } finally { setSaving(false); }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.75)", backdropFilter: "blur(8px)" }}
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <div className="card w-full max-w-lg shadow-2xl"
        style={{ boxShadow: "0 0 60px rgba(124,109,255,0.2), 0 24px 64px rgba(0,0,0,0.5)" }}>
        <div className="card-glow" />
        <div className="flex items-center justify-between p-5 border-b border-border">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-[rgba(124,109,255,0.12)] flex items-center justify-center text-[#7c6dff]">
              <Pencil size={16} />
            </div>
            <div>
              <h2 className="text-base font-bold text-text-main m-0">Edit Application</h2>
              <p className="text-xs text-text-subtle mt-0.5">{app.company_name}</p>
            </div>
          </div>
          <button onClick={onClose} className="w-8 h-8 rounded-full flex items-center justify-center text-text-muted hover:bg-neutral-soft hover:text-text-main transition-colors">
            <X size={16} />
          </button>
        </div>

        <form onSubmit={handleSave} className="p-5 flex flex-col gap-4">
          {error && (
            <div className="flex items-center gap-2 text-sm text-danger bg-danger-soft border border-danger/20 rounded-lg px-4 py-3">
              <AlertCircle size={15} /> {error}
            </div>
          )}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-semibold uppercase tracking-wider text-text-subtle">Company *</label>
              <input value={form.company_name} onChange={e => set("company_name", e.target.value)} required placeholder="Company name" />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-semibold uppercase tracking-wider text-text-subtle">Role</label>
              <input value={form.role} onChange={e => set("role", e.target.value)} placeholder="e.g. Software Engineer" />
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-semibold uppercase tracking-wider text-text-subtle">Job URL</label>
            <input value={form.job_url} onChange={e => set("job_url", e.target.value)} placeholder="https://..." />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-semibold uppercase tracking-wider text-text-subtle">Job Board</label>
              <input value={form.job_board} onChange={e => set("job_board", e.target.value)} placeholder="LinkedIn, Indeed…" />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-semibold uppercase tracking-wider text-text-subtle">Status</label>
              <select value={form.status} onChange={e => set("status", e.target.value)}>
                {ALL_STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-semibold uppercase tracking-wider text-text-subtle">Notes</label>
            <textarea value={form.notes} onChange={e => set("notes", e.target.value)} rows={3} placeholder="Any notes…" className="resize-none" />
          </div>

          <div className="flex items-center justify-end gap-3 pt-2 border-t border-border mt-1">
            <button type="button" onClick={onClose} className="btn-ghost px-4 py-2 text-sm">Cancel</button>
            <button type="submit" disabled={saving} className="btn px-5 py-2 text-sm">
              {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
              {saving ? "Saving…" : "Save Changes"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/* ─────────────────── App Card (Grid) ─────────────────── */
function AppCard({ app, onRefresh }) {
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [deleting, setDeleting]           = useState(false);
  const [editing, setEditing]             = useState(false);
  const [statusSaving, setStatusSaving]   = useState(false);

  const updateStatus = async (newStatus) => {
    setStatusSaving(true);
    try { await api.patch(`/api/applications/${app.id}`, { status: newStatus }); onRefresh(); }
    catch (err) { console.error(err); }
    finally { setStatusSaving(false); }
  };

  const handleDelete = async () => {
    setDeleting(true); setDeleteConfirm(false);
    try { await api.delete(`/api/applications/${app.id}`); onRefresh(); }
    catch (err) { console.error("Delete failed:", err); setDeleteConfirm(true); setDeleting(false); }
  };

  return (
    <>
      {editing && <EditModal app={app} onClose={() => setEditing(false)} onSaved={onRefresh} />}
      <div className={`card group flex flex-col p-5 transition-all duration-300 ${deleteConfirm ? "border-danger/50 shadow-[0_0_0_1px_rgba(224,80,80,0.2)]" : ""}`}>
        <div className="card-glow" />

        <div className="flex justify-between items-start mb-3">
          <div className="flex-1 pr-3 min-w-0">
            <div className="flex items-center gap-2 mb-0.5">
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: STATUS_DOT[app.status] || "#6b7280" }} />
              <h3 className="text-base font-bold text-text-main leading-tight truncate" title={app.company_name}>
                {app.company_name}
              </h3>
            </div>
            <div className="text-sm text-text-subtle mt-0.5 truncate pl-4">{app.role || "General Application"}</div>
          </div>

          <div className={`flex items-center gap-1 shrink-0 transition-opacity duration-200 ${deleteConfirm ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`}>
            {!deleteConfirm && (
              <>
                {app.job_url && (
                  <a href={app.job_url} target="_blank" rel="noreferrer"
                    className="w-7 h-7 rounded-lg flex items-center justify-center text-text-muted hover:text-[#7c6dff] hover:bg-[rgba(124,109,255,0.1)] transition-colors">
                    <ExternalLink size={13} />
                  </a>
                )}
                <button onClick={() => setEditing(true)}
                  className="w-7 h-7 rounded-lg flex items-center justify-center text-text-muted hover:text-[#7c6dff] hover:bg-[rgba(124,109,255,0.1)] transition-colors" title="Edit">
                  <Pencil size={13} />
                </button>
                <button onClick={() => setDeleteConfirm(true)}
                  className="w-7 h-7 rounded-lg flex items-center justify-center text-text-muted hover:text-danger hover:bg-danger-soft transition-colors" title="Delete">
                  <Trash2 size={13} />
                </button>
              </>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 mb-4">
          <select
            value={app.status || "Applied"}
            onChange={e => updateStatus(e.target.value)}
            disabled={statusSaving}
            className={`text-xs font-bold px-3 py-1 rounded-full border focus:outline-none focus:ring-2 focus:ring-[rgba(124,109,255,0.3)] cursor-pointer transition-all appearance-none ${STATUS_STYLE[app.status] || "bg-neutral-soft text-text-main border-border"}`}
          >
            {ALL_STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          {app.job_board && (
            <span className="text-[10px] font-bold tracking-widest uppercase text-text-subtle bg-neutral-soft px-2 py-0.5 rounded-md border border-border">
              {app.job_board}
            </span>
          )}
          {statusSaving && <Loader2 size={12} className="animate-spin text-text-subtle" />}
        </div>

        {app.notes ? (
          <p className="text-sm text-text-muted line-clamp-2 leading-relaxed flex-1 mb-4">{app.notes}</p>
        ) : (
          <p className="text-sm text-text-subtle/40 italic flex-1 mb-4">No notes provided.</p>
        )}

        <div className="pt-3 border-t border-border">
          {deleteConfirm ? (
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-semibold text-danger flex items-center gap-1.5">
                <AlertCircle size={13} /> Delete this application?
              </span>
              <div className="flex items-center gap-2">
                <button onClick={() => setDeleteConfirm(false)} className="btn-ghost !px-3 !py-1 !min-h-[28px] !text-xs">Cancel</button>
                <button onClick={handleDelete} disabled={deleting} className="btn-danger !px-3 !py-1 !min-h-[28px] !text-xs">
                  {deleting ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                  {deleting ? "Deleting…" : "Delete"}
                </button>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-xs font-medium text-text-subtle">
                <Calendar size={12} />
                {new Date(app.applied_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
              </div>
              <Link
                href={`/campaign-builder?company=${encodeURIComponent(app.company_name)}&job_application_id=${encodeURIComponent(app.id)}`}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold text-[#7c6dff] bg-[rgba(124,109,255,0.1)] hover:bg-[#7c6dff] hover:text-white border border-[rgba(124,109,255,0.2)] rounded-lg transition-all duration-200"
              >
                <Plus size={12} /> Add Lead
              </Link>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

/* ─────────────────── App Row (Compact List) ─────────────────── */
function AppRow({ app, onRefresh }) {
  const [editing, setEditing]           = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [deleting, setDeleting]           = useState(false);
  const [statusSaving, setStatusSaving]   = useState(false);

  const updateStatus = async (newStatus) => {
    setStatusSaving(true);
    try { await api.patch(`/api/applications/${app.id}`, { status: newStatus }); onRefresh(); }
    catch (err) { console.error(err); }
    finally { setStatusSaving(false); }
  };

  const handleDelete = async () => {
    setDeleting(true); setDeleteConfirm(false);
    try { await api.delete(`/api/applications/${app.id}`); onRefresh(); }
    catch (err) { console.error(err); setDeleteConfirm(true); setDeleting(false); }
  };

  return (
    <>
      {editing && <EditModal app={app} onClose={() => setEditing(false)} onSaved={onRefresh} />}
      <div className={`group flex items-center gap-3 px-4 py-3 border-b border-border hover:bg-neutral-soft/40 transition-colors ${deleteConfirm ? "bg-danger-soft/20" : ""}`}>
        {/* Status dot */}
        <span className="w-2 h-2 rounded-full shrink-0" style={{ background: STATUS_DOT[app.status] || "#6b7280" }} />

        {/* Company + Role */}
        <div className="flex-1 min-w-0 grid grid-cols-2 gap-2">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="font-semibold text-sm text-text-main truncate">{app.company_name}</span>
            {app.job_url && (
              <a href={app.job_url} target="_blank" rel="noreferrer"
                className="shrink-0 opacity-0 group-hover:opacity-100 text-text-muted hover:text-[#7c6dff] transition-all">
                <ExternalLink size={11} />
              </a>
            )}
          </div>
          <span className="text-sm text-text-subtle truncate">{app.role || "—"}</span>
        </div>

        {/* Job board */}
        <span className="hidden md:block text-xs text-text-subtle bg-neutral-soft px-2 py-0.5 rounded border border-border w-24 text-center truncate">
          {app.job_board || "—"}
        </span>

        {/* Status select */}
        <select
          value={app.status || "Applied"}
          onChange={e => updateStatus(e.target.value)}
          disabled={statusSaving}
          className={`text-xs font-bold px-2.5 py-1 rounded-full border cursor-pointer appearance-none focus:outline-none w-36 ${STATUS_STYLE[app.status] || "bg-neutral-soft text-text-main border-border"}`}
        >
          {ALL_STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
        </select>

        {/* Date */}
        <span className="hidden lg:flex items-center gap-1 text-xs text-text-subtle w-20 shrink-0">
          <Calendar size={11} />
          {new Date(app.applied_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
        </span>

        {/* Actions */}
        <div className="flex items-center gap-1 shrink-0">
          {deleteConfirm ? (
            <>
              <button onClick={() => setDeleteConfirm(false)} className="text-xs text-text-muted hover:text-text-main px-2">Cancel</button>
              <button onClick={handleDelete} disabled={deleting} className="text-xs font-bold text-danger hover:bg-danger-soft px-2 py-1 rounded">
                {deleting ? <Loader2 size={11} className="animate-spin" /> : "Delete"}
              </button>
            </>
          ) : (
            <>
              <Link
                href={`/campaign-builder?company=${encodeURIComponent(app.company_name)}&job_application_id=${encodeURIComponent(app.id)}`}
                className="opacity-0 group-hover:opacity-100 w-7 h-7 rounded-lg flex items-center justify-center text-text-muted hover:text-[#7c6dff] hover:bg-[rgba(124,109,255,0.1)] transition-all"
                title="Add Lead"
              >
                <Plus size={13} />
              </Link>
              <button onClick={() => setEditing(true)}
                className="opacity-0 group-hover:opacity-100 w-7 h-7 rounded-lg flex items-center justify-center text-text-muted hover:text-[#7c6dff] hover:bg-[rgba(124,109,255,0.1)] transition-all" title="Edit">
                <Pencil size={13} />
              </button>
              <button onClick={() => setDeleteConfirm(true)}
                className="opacity-0 group-hover:opacity-100 w-7 h-7 rounded-lg flex items-center justify-center text-text-muted hover:text-danger hover:bg-danger-soft transition-all" title="Delete">
                <Trash2 size={13} />
              </button>
            </>
          )}
        </div>
      </div>
    </>
  );
}

/* ─────────────────── Stats Bar ─────────────────── */
// counts is always from the FULL unfiltered dataset, passed in by the page
function StatsBar({ counts = {}, activeStatus, onStatusClick }) {
  return (
    <div className="flex flex-wrap gap-2 mb-5">
      {ALL_STATUSES.map(s => {
        const active = activeStatus.includes(s);
        const n = counts[s] ?? 0;
        return (
          <button
            key={s}
            onClick={() => onStatusClick(s)}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-bold border transition-all duration-150 ${
              active
                ? "border-[rgba(124,109,255,0.5)] bg-[rgba(124,109,255,0.12)] text-[#7c6dff]"
                : "border-border bg-neutral-soft text-text-subtle hover:text-text-main hover:border-border"
            }`}
          >
            <span className="w-2 h-2 rounded-full" style={{ background: STATUS_DOT[s] }} />
            {s}
            <span className={`ml-0.5 px-1.5 py-0.5 rounded-md text-[10px] font-extrabold ${
              active ? "bg-[rgba(124,109,255,0.2)] text-[#7c6dff]" : "bg-border/50 text-text-subtle"
            }`}>
              {n}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/* ─────────────────── Page ─────────────────── */
export default function ApplicationsPage() {
  const [applications, setApplications] = useState([]);
  const [boards, setBoards]             = useState([]);
  const [loading, setLoading]           = useState(true);
  const [viewMode, setViewMode]         = useState("grid"); // "grid" | "list"
  const [page, setPage]                 = useState(1);

  // Unfiltered stats — always reflects total dataset regardless of active filters
  const [stats, setStats]           = useState({});
  const [totalCount, setTotalCount] = useState(0);

  // Filter state
  const [search, setSearch]         = useState("");
  const [activeStatus, setActiveStatus] = useState([]); // multi-select
  const [datePreset, setDatePreset] = useState("all");
  const [jobBoard, setJobBoard]     = useState("");
  const [sort, setSort]             = useState("newest");

  // Debounced search
  const searchTimer = useRef(null);
  const [debouncedSearch, setDebouncedSearch] = useState("");

  useEffect(() => {
    clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(searchTimer.current);
  }, [search]);

  const fetchBoards = useCallback(() => {
    api.get("/api/applications/boards")
      .then(res => setBoards(res.data.boards || []))
      .catch(() => {});
  }, []);

  // Fetch full unfiltered dataset just for computing status counts
  const fetchStats = useCallback(() => {
    api.get("/api/applications")
      .then(res => {
        const apps = res.data.applications || [];
        const c = {};
        for (const s of ALL_STATUSES) c[s] = 0;
        for (const a of apps) c[a.status] = (c[a.status] || 0) + 1;
        setStats(c);
        setTotalCount(apps.length);
      })
      .catch(() => {});
  }, []);

  const fetchApplications = useCallback(() => {
    const params = new URLSearchParams();
    if (debouncedSearch) params.set("search", debouncedSearch);
    if (activeStatus.length) params.set("status", activeStatus.join(","));
    if (jobBoard) params.set("job_board", jobBoard);
    if (sort) params.set("sort", sort);
    const { date_from } = getDateRange(datePreset);
    if (date_from) params.set("date_from", date_from);

    setLoading(true);
    api.get(`/api/applications?${params.toString()}`)
      .then(res => {
        setApplications(res.data.applications || []);
        setPage(1);
      })
      .catch(err => console.error(err))
      .finally(() => setLoading(false));
  }, [debouncedSearch, activeStatus, jobBoard, sort, datePreset]);

  // Combined refresh: re-fetch both filtered list AND global stats
  const refresh = useCallback(() => {
    fetchApplications();
    fetchStats();
    fetchBoards();
  }, [fetchApplications, fetchStats, fetchBoards]);

  useEffect(() => { fetchBoards(); }, [fetchBoards]);
  useEffect(() => { fetchStats(); }, [fetchStats]);
  useEffect(() => { fetchApplications(); }, [fetchApplications]);

  const handleStatusClick = (s) => {
    setActiveStatus(prev =>
      prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]
    );
  };

  const activeFilterCount = [
    debouncedSearch ? 1 : 0,
    activeStatus.length > 0 ? 1 : 0,
    datePreset !== "all" ? 1 : 0,
    jobBoard ? 1 : 0,
  ].reduce((a, b) => a + b, 0);

  const clearAllFilters = () => {
    setSearch(""); setActiveStatus([]); setDatePreset("all"); setJobBoard(""); setSort("newest");
  };

  // Pagination
  const totalPages = Math.max(1, Math.ceil(applications.length / PAGE_SIZE));
  const paged = applications.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  return (
    <div className="w-[min(1200px,calc(100vw-32px))] mx-auto pb-20">
      <TopNav />
      <main>
        {/* Page header */}
        <div className="mb-6 flex items-end justify-between gap-4">
          <div>
            <h1
              className="text-3xl font-extrabold tracking-tight flex items-center gap-3"
              style={{ background: "linear-gradient(120deg, var(--text-main) 0%, #7c6dff 100%)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}
            >
              <Briefcase size={26} className="text-[#7c6dff]" style={{ WebkitTextFillColor: "initial" }} />
              Job Applications
            </h1>
            <p className="text-text-muted mt-1.5 text-sm">
              {totalCount} total
              {activeFilterCount > 0 && applications.length !== totalCount
                ? ` · ${applications.length} matching filters`
                : ""}
            </p>
          </div>

          {/* View toggle */}
          <div className="flex items-center gap-2 shrink-0">
            <div className="flex items-center bg-neutral-soft border border-border rounded-lg p-1">
              <button
                onClick={() => setViewMode("grid")}
                className={`p-1.5 rounded-md transition-colors ${viewMode === "grid" ? "bg-[rgba(124,109,255,0.15)] text-[#7c6dff]" : "text-text-muted hover:text-text-main"}`}
                title="Card view"
              >
                <LayoutGrid size={15} />
              </button>
              <button
                onClick={() => setViewMode("list")}
                className={`p-1.5 rounded-md transition-colors ${viewMode === "list" ? "bg-[rgba(124,109,255,0.15)] text-[#7c6dff]" : "text-text-muted hover:text-text-main"}`}
                title="Compact list"
              >
                <List size={15} />
              </button>
            </div>
          </div>
        </div>

        {/* ── Stats pills — counts always reflect the full unfiltered dataset ── */}
        <StatsBar counts={stats} activeStatus={activeStatus} onStatusClick={handleStatusClick} />

        {/* ── Filter bar ── */}
        <div className="card mb-6">
          <div className="card-glow" />
          <div className="p-4 flex flex-wrap items-center gap-3">
            {/* Search */}
            <div className="relative flex-1 min-w-[180px]">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-subtle pointer-events-none" />
              <input
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Search company or role…"
                className="pl-9 pr-4 py-2 text-sm w-full"
              />
              {search && (
                <button onClick={() => setSearch("")} className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-main">
                  <X size={13} />
                </button>
              )}
            </div>

            {/* Date preset */}
            <div className="flex items-center bg-neutral-soft border border-border rounded-lg overflow-hidden">
              {DATE_PRESETS.map(p => (
                <button
                  key={p.value}
                  onClick={() => setDatePreset(p.value)}
                  className={`px-3 py-2 text-xs font-semibold transition-colors ${
                    datePreset === p.value
                      ? "bg-[rgba(124,109,255,0.15)] text-[#7c6dff]"
                      : "text-text-subtle hover:text-text-main"
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>

            {/* Job board filter */}
            {boards.length > 0 && (
              <select
                value={jobBoard}
                onChange={e => setJobBoard(e.target.value)}
                className="text-sm py-2 px-3 min-w-[130px]"
              >
                <option value="">All boards</option>
                {boards.map(b => <option key={b} value={b}>{b}</option>)}
              </select>
            )}

            {/* Sort */}
            <div className="relative">
              <ArrowUpDown size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-subtle pointer-events-none" />
              <select
                value={sort}
                onChange={e => setSort(e.target.value)}
                className="text-sm pl-8 py-2 pr-3"
              >
                <option value="newest">Newest first</option>
                <option value="oldest">Oldest first</option>
                <option value="company">Company A-Z</option>
              </select>
            </div>

            {/* Active filter count + clear */}
            {activeFilterCount > 0 && (
              <button
                onClick={clearAllFilters}
                className="flex items-center gap-1.5 text-xs font-bold text-[#7c6dff] hover:text-danger px-3 py-2 rounded-lg border border-[rgba(124,109,255,0.2)] bg-[rgba(124,109,255,0.06)] hover:bg-danger-soft hover:border-danger/20 transition-all"
              >
                <Filter size={12} />
                {activeFilterCount} filter{activeFilterCount !== 1 ? "s" : ""} active
                <X size={11} />
              </button>
            )}
          </div>
        </div>

        {/* ── Results ── */}
        {loading ? (
          <div className="card flex flex-col items-center justify-center p-20">
            <div className="card-glow" />
            <div className="w-8 h-8 rounded-full border-2 border-[rgba(124,109,255,0.2)] border-t-[#7c6dff] animate-spin mb-4" />
            <p className="text-text-muted font-medium animate-pulse">Loading applications…</p>
          </div>
        ) : applications.length === 0 ? (
          <div className="card flex flex-col items-center justify-center p-20 text-center">
            <div className="card-glow" />
            <div className="w-14 h-14 rounded-2xl bg-[rgba(124,109,255,0.1)] flex items-center justify-center mb-4 text-[#7c6dff]">
              {activeFilterCount > 0 ? <Filter size={28} /> : <Briefcase size={28} />}
            </div>
            <h3 className="text-xl font-bold text-text-main mb-2">
              {activeFilterCount > 0 ? "No matching applications" : "No applications yet"}
            </h3>
            <p className="text-text-muted max-w-md text-sm">
              {activeFilterCount > 0
                ? "Try adjusting your filters or clearing them to see all applications."
                : "Use the Mailer Chrome Extension on job boards like LinkedIn to record your first application instantly."}
            </p>
            {activeFilterCount > 0 && (
              <button onClick={clearAllFilters} className="btn mt-5 px-5 py-2 text-sm">
                Clear all filters
              </button>
            )}
          </div>
        ) : viewMode === "grid" ? (
          <>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
              {paged.map(app => (
                <AppCard key={app.id} app={app} onRefresh={refresh} />
              ))}
            </div>
          </>
        ) : (
          /* ── Compact list view ── */
          <div className="card overflow-hidden">
            <div className="card-glow" />
            {/* List header */}
            <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border bg-neutral-soft/50">
              <span className="w-2 shrink-0" />
              <div className="flex-1 grid grid-cols-2 gap-2 min-w-0">
                <span className="text-[10px] font-bold uppercase tracking-widest text-text-subtle">Company</span>
                <span className="text-[10px] font-bold uppercase tracking-widest text-text-subtle">Role</span>
              </div>
              <span className="hidden md:block text-[10px] font-bold uppercase tracking-widest text-text-subtle w-24 text-center">Board</span>
              <span className="text-[10px] font-bold uppercase tracking-widest text-text-subtle w-36">Status</span>
              <span className="hidden lg:block text-[10px] font-bold uppercase tracking-widest text-text-subtle w-20">Applied</span>
              <span className="w-24 shrink-0" />
            </div>
            {paged.map(app => (
              <AppRow key={app.id} app={app} onRefresh={refresh} />
            ))}
          </div>
        )}

        {/* ── Pagination ── */}
        {!loading && totalPages > 1 && (
          <div className="flex items-center justify-between mt-6">
            <span className="text-sm text-text-subtle">
              Showing {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, applications.length)} of {applications.length}
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page === 1}
                className="btn-ghost px-3 py-2 text-sm disabled:opacity-40 flex items-center gap-1.5"
              >
                <ChevronLeft size={14} /> Prev
              </button>

              {/* Page number pills */}
              <div className="flex items-center gap-1">
                {Array.from({ length: totalPages }, (_, i) => i + 1)
                  .filter(n => n === 1 || n === totalPages || Math.abs(n - page) <= 1)
                  .reduce((acc, n, idx, arr) => {
                    if (idx > 0 && n - arr[idx - 1] > 1) acc.push("…");
                    acc.push(n);
                    return acc;
                  }, [])
                  .map((item, idx) =>
                    item === "…" ? (
                      <span key={`ellipsis-${idx}`} className="px-1 text-text-subtle text-sm">…</span>
                    ) : (
                      <button
                        key={item}
                        onClick={() => setPage(item)}
                        className={`w-8 h-8 rounded-lg text-sm font-bold transition-colors ${
                          page === item
                            ? "bg-[rgba(124,109,255,0.15)] text-[#7c6dff] border border-[rgba(124,109,255,0.3)]"
                            : "text-text-muted hover:bg-neutral-soft hover:text-text-main"
                        }`}
                      >
                        {item}
                      </button>
                    )
                  )}
              </div>

              <button
                onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                disabled={page === totalPages}
                className="btn-ghost px-3 py-2 text-sm disabled:opacity-40 flex items-center gap-1.5"
              >
                Next <ChevronRight size={14} />
              </button>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
