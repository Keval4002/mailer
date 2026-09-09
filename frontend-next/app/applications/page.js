"use client";

import { useEffect, useState } from "react";
import api from "@/lib/api";
import {
  Briefcase, ExternalLink, Plus, Calendar, Pencil, Trash2, X,
  Loader2, CheckCircle2, AlertCircle, Save
} from "lucide-react";
import TopNav from "@/app/components/TopNav";
import Link from "next/link";

const ALL_STATUSES = ["Applied", "Emailed", "Got a reply", "Interview scheduled", "Rejected", "Offer"];

const STATUS_STYLE = {
  "Applied":              "status-applied",
  "Emailed":              "status-emailed",
  "Got a reply":          "status-reply",
  "Interview scheduled":  "status-interview",
  "Rejected":             "status-rejected",
  "Offer":                "status-offer",
};

/* ── Edit Modal ── */
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
    setSaving(true);
    setError(null);
    try {
      await api.patch(`/api/applications/${app.id}`, form);
      onSaved();
      onClose();
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to save.");
    } finally {
      setSaving(false);
    }
  };

  return (
    /* Backdrop */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.7)", backdropFilter: "blur(8px)" }}
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <div
        className="card w-full max-w-lg shadow-2xl"
        style={{ boxShadow: "0 0 60px rgba(124,109,255,0.2), 0 24px 64px rgba(0,0,0,0.5)" }}
      >
        <div className="card-glow" />
        {/* Header */}
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

        {/* Form */}
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

/* ── Application Card ── */
function AppCard({ app, onRefresh }) {
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [deleting, setDeleting]           = useState(false);
  const [editing, setEditing]             = useState(false);
  const [statusSaving, setStatusSaving]   = useState(false);

  const updateStatus = async (newStatus) => {
    setStatusSaving(true);
    try {
      await api.patch(`/api/applications/${app.id}`, { status: newStatus });
      onRefresh();
    } catch (err) {
      console.error(err);
    } finally {
      setStatusSaving(false);
    }
  };

  const handleDelete = async () => {
    setDeleting(true);
    // Optimistic: dismiss confirm immediately so UX feels instant
    setDeleteConfirm(false);
    try {
      await api.delete(`/api/applications/${app.id}`);
      onRefresh(); // re-fetch list (card is gone from server now)
    } catch (err) {
      console.error("Delete failed:", err);
      // Restore confirm state so user can retry
      setDeleteConfirm(true);
      setDeleting(false);
    }
  };

  return (
    <>
      {editing && (
        <EditModal
          app={app}
          onClose={() => setEditing(false)}
          onSaved={onRefresh}
        />
      )}

      <div
        className={`card group flex flex-col p-5 transition-all duration-300 ${
          deleteConfirm ? "border-danger/50 shadow-[0_0_0_1px_rgba(224,80,80,0.2)]" : ""
        }`}
      >
        <div className="card-glow" />

        {/* Header row */}
        <div className="flex justify-between items-start mb-3">
          <div className="flex-1 pr-3 min-w-0">
            <h3 className="text-base font-bold text-text-main leading-tight truncate" title={app.company_name}>
              {app.company_name}
            </h3>
            <div className="text-sm text-text-subtle mt-0.5 truncate">
              {app.role || "General Application"}
            </div>
          </div>

          {/* Action buttons — always visible on delete confirm, else hover */}
          <div className={`flex items-center gap-1 shrink-0 transition-opacity duration-200 ${deleteConfirm ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`}>
            {!deleteConfirm && (
              <>
                {app.job_url && (
                  <a
                    href={app.job_url}
                    target="_blank"
                    rel="noreferrer"
                    className="w-7 h-7 rounded-lg flex items-center justify-center text-text-muted hover:text-[#7c6dff] hover:bg-[rgba(124,109,255,0.1)] transition-colors"
                  >
                    <ExternalLink size={13} />
                  </a>
                )}
                <button
                  onClick={() => setEditing(true)}
                  className="w-7 h-7 rounded-lg flex items-center justify-center text-text-muted hover:text-[#7c6dff] hover:bg-[rgba(124,109,255,0.1)] transition-colors"
                  title="Edit"
                >
                  <Pencil size={13} />
                </button>
                <button
                  onClick={() => setDeleteConfirm(true)}
                  className="w-7 h-7 rounded-lg flex items-center justify-center text-text-muted hover:text-danger hover:bg-danger-soft transition-colors"
                  title="Delete"
                >
                  <Trash2 size={13} />
                </button>
              </>
            )}
          </div>
        </div>

        {/* Status row */}
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

        {/* Notes */}
        <div className="flex-1 mb-4">
          {app.notes ? (
            <p className="text-sm text-text-muted line-clamp-3 leading-relaxed">{app.notes}</p>
          ) : (
            <p className="text-sm text-text-subtle/40 italic">No notes provided.</p>
          )}
        </div>

        {/* Footer */}
        <div className="pt-3 border-t border-border">
          {deleteConfirm ? (
            /* ── Inline delete confirm ── */
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-semibold text-danger flex items-center gap-1.5">
                <AlertCircle size={13} /> Delete this application?
              </span>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setDeleteConfirm(false)}
                  className="btn-ghost !px-3 !py-1 !min-h-[28px] !text-xs"
                >
                  Cancel
                </button>
                <button
                  onClick={handleDelete}
                  disabled={deleting}
                  className="btn-danger !px-3 !py-1 !min-h-[28px] !text-xs"
                >
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
                <Plus size={12} />
                Add Lead
              </Link>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

/* ── Page ── */
export default function ApplicationsPage() {
  const [applications, setApplications] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchApplications = () => {
    api
      .get("/api/applications")
      .then(res => {
        setApplications(res.data.applications || []);
        setLoading(false);
      })
      .catch(err => {
        console.error(err);
        setLoading(false);
      });
  };

  useEffect(() => { fetchApplications(); }, []);

  return (
    <div className="w-[min(1100px,calc(100vw-32px))] mx-auto pb-20">
      <TopNav />
      <main>
        {/* Page header */}
        <div className="mb-8 flex items-end justify-between gap-4">
          <div>
            <h1 className="text-3xl font-extrabold tracking-tight flex items-center gap-3"
              style={{ background: "linear-gradient(120deg, var(--text-main) 0%, #7c6dff 100%)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
              <Briefcase size={26} className="text-[#7c6dff]" style={{ WebkitTextFillColor: "initial" }} />
              Job Applications
            </h1>
            <p className="text-text-muted mt-2 text-sm">Track your progress and connect leads to active applications.</p>
          </div>
          {!loading && applications.length > 0 && (
            <div className="text-right shrink-0">
              <div className="text-2xl font-bold text-text-main">{applications.length}</div>
              <div className="text-[10px] font-bold uppercase tracking-widest text-text-subtle">Total Tracked</div>
            </div>
          )}
        </div>

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
              <Briefcase size={28} />
            </div>
            <h3 className="text-xl font-bold text-text-main mb-2">No applications yet</h3>
            <p className="text-text-muted max-w-md text-sm">
              Use the Mailer Chrome Extension on job boards like LinkedIn to record your first application instantly.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {applications.map(app => (
              <AppCard key={app.id} app={app} onRefresh={fetchApplications} />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
