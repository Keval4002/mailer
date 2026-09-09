"use client";

import { useEffect, useState, useRef } from "react";
import ReactMarkdown from "react-markdown";
import api from "@/lib/api";
import { StickyNote, X, Image as ImageIcon, Loader2, Plus, Calendar } from "lucide-react";
import TopNav from "@/app/components/TopNav";

export default function RemindersPage() {
  const [reminders,   setReminders]   = useState([]);
  const [loading,     setLoading]     = useState(true);
  const [creating,    setCreating]    = useState(false);
  const [newTitle,    setNewTitle]    = useState("");
  const [newContent,  setNewContent]  = useState("");
  const [deleteTarget,setDeleteTarget]= useState(null); // id of note pending deletion
  const [deleting,    setDeleting]    = useState(false);
  const fileInputRef = useRef(null);

  const fetchReminders = async () => {
    try {
      const res = await api.get("/api/reminders");
      setReminders(res.data.reminders || []);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchReminders(); }, []);

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!newTitle.trim() || !newContent.trim()) return;
    setCreating(true);
    try {
      await api.post("/api/reminders", { title: newTitle, content_text: newContent });
      setNewTitle("");
      setNewContent("");
      await fetchReminders();
    } catch (err) {
      console.error(err);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id) => {
    setDeleting(true);
    try {
      await api.delete(`/api/reminders/${id}`);
      setReminders(prev => prev.filter(r => r.id !== id));
    } catch (err) {
      console.error(err);
    } finally {
      setDeleting(false);
      setDeleteTarget(null);
    }
  };

  const handleImageUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await api.post("/api/upload-image", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setNewContent(prev => prev + `\n\n![Image](${res.data.url})`);
    } catch {
      alert("Failed to upload image.");
    }
  };

  return (
    <div className="w-[min(1100px,calc(100vw-32px))] mx-auto pb-20">
      <TopNav />
      <main>
        {/* Page header */}
        <div className="mb-8">
          <h1 className="text-3xl font-extrabold tracking-tight flex items-center gap-3"
            style={{ background: "linear-gradient(120deg, var(--text-main) 0%, #7c6dff 100%)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
            <StickyNote size={26} className="text-[#7c6dff]" style={{ WebkitTextFillColor: "initial" }} />
            Notes & Reminders
          </h1>
          <p className="text-text-muted mt-2 text-sm">Jot down notes, paste updates, keep track of anything job-related.</p>
        </div>

        <div className="space-y-8">
          {/* Create card */}
          <div className="card overflow-hidden">
            <div className="card-glow" />
            <div className="flex items-center gap-3 p-5 border-b border-border">
              <div className="w-8 h-8 rounded-lg bg-[rgba(124,109,255,0.1)] flex items-center justify-center text-[#7c6dff]">
                <Plus size={16} />
              </div>
              <h2 className="m-0 text-base font-bold text-text-main">New Note</h2>
            </div>
            <form onSubmit={handleCreate} className="p-5 flex flex-col gap-4">
              <input
                type="text"
                placeholder="Title (e.g. Upcoming Interview at XYZ)"
                value={newTitle}
                onChange={e => setNewTitle(e.target.value)}
                className="text-base font-semibold !bg-neutral-soft !border-transparent focus:!bg-surface focus:!border-[rgba(124,109,255,0.4)] rounded-lg px-4 py-3 transition-all"
                required
              />
              <div className="relative">
                <textarea
                  placeholder="Jot down notes, paste updates, use Markdown…"
                  value={newContent}
                  onChange={e => setNewContent(e.target.value)}
                  className="w-full min-h-[140px] p-4 pb-12 !bg-neutral-soft !border-transparent focus:!bg-surface focus:!border-[rgba(124,109,255,0.4)] rounded-lg resize-none transition-all"
                  required
                />
                <div className="absolute bottom-3 left-3 flex gap-2">
                  <input type="file" accept="image/*" className="hidden" ref={fileInputRef} onChange={handleImageUpload} />
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="btn-ghost !px-2.5 !py-1 !min-h-[28px] !text-xs !gap-1.5"
                    title="Upload Image"
                  >
                    <ImageIcon size={12} /> Image
                  </button>
                </div>
                <button
                  type="submit"
                  disabled={creating}
                  className="btn absolute bottom-3 right-3 !px-4 !py-1 !min-h-[28px] !text-xs"
                >
                  {creating ? <Loader2 size={12} className="animate-spin" /> : null}
                  {creating ? "Saving…" : "Save Note"}
                </button>
              </div>
            </form>
          </div>

          {/* Notes grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {loading ? (
              <div className="col-span-full py-16 text-center text-text-muted flex flex-col items-center gap-3">
                <div className="w-7 h-7 rounded-full border-2 border-[rgba(124,109,255,0.2)] border-t-[#7c6dff] animate-spin" />
                Loading notes…
              </div>
            ) : reminders.length === 0 ? (
              <div className="col-span-full card flex flex-col items-center justify-center p-16 text-center">
                <div className="card-glow" />
                <div className="w-14 h-14 rounded-2xl bg-[rgba(124,109,255,0.08)] flex items-center justify-center mb-4 text-[#7c6dff]">
                  <StickyNote size={28} />
                </div>
                <p className="text-text-muted text-sm">No notes yet. Add one above!</p>
              </div>
            ) : (
              reminders.map(r => (
                <div
                  key={r.id}
                  className={`card group flex flex-col p-5 transition-all duration-300 ${
                    deleteTarget === r.id ? "border-danger/50 shadow-[0_0_0_1px_rgba(224,80,80,0.2)]" : ""
                  }`}
                >
                  <div className="card-glow" />

                  {/* Note header */}
                  <div className="flex items-start justify-between gap-2 mb-3">
                    <h3 className="font-bold text-base text-text-main leading-snug pr-1 flex-1 truncate">{r.title}</h3>
                    {deleteTarget !== r.id && (
                      <button
                        onClick={() => setDeleteTarget(r.id)}
                        className="w-7 h-7 rounded-lg shrink-0 flex items-center justify-center text-text-muted hover:bg-danger-soft hover:text-danger opacity-0 group-hover:opacity-100 transition-all"
                      >
                        <X size={14} />
                      </button>
                    )}
                  </div>

                  {/* Content */}
                  <div className="text-sm text-text-muted flex-1 overflow-hidden prose prose-sm dark:prose-invert max-w-none line-clamp-6 mb-4">
                    <ReactMarkdown>{r.content_text}</ReactMarkdown>
                  </div>

                  {/* Footer */}
                  <div className="pt-3 border-t border-border mt-auto">
                    {deleteTarget === r.id ? (
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-semibold text-danger flex items-center gap-1.5">
                          Delete this note?
                        </span>
                        <div className="flex gap-2">
                          <button
                            onClick={() => setDeleteTarget(null)}
                            className="btn-ghost !px-3 !py-1 !min-h-[26px] !text-xs"
                          >
                            Cancel
                          </button>
                          <button
                            onClick={() => handleDelete(r.id)}
                            disabled={deleting}
                            className="btn-danger !px-3 !py-1 !min-h-[26px] !text-xs"
                          >
                            {deleting ? <Loader2 size={11} className="animate-spin" /> : <X size={11} />}
                            Delete
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex items-center gap-1.5 text-xs text-text-subtle">
                        <Calendar size={11} />
                        {new Date(r.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}
                      </div>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
