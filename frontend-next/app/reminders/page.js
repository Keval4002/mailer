"use client";

import { useEffect, useState, useRef } from "react";
import ReactMarkdown from "react-markdown";
import api from "@/lib/api";
import { StickyNote, X, Image as ImageIcon, Loader2 } from "lucide-react";
import TopNav from "@/app/components/TopNav";

export default function RemindersPage() {
  const [reminders, setReminders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newContent, setNewContent] = useState("");
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

  useEffect(() => {
    fetchReminders();
  }, []);

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
    try {
      await api.delete(`/api/reminders/${id}`);
      setReminders(reminders.filter((r) => r.id !== id));
    } catch (err) {
      console.error(err);
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
      const imageUrl = res.data.url;
      setNewContent((prev) => prev + `\n\n![Image](${imageUrl})`);
    } catch (err) {
      console.error("Image upload failed", err);
      alert("Failed to upload image.");
    }
  };

  return (
    <div className="w-[min(1100px,calc(100vw-32px))] mx-auto pb-20">
      <TopNav />
      <main>
        <div className="space-y-6">
          {/* Create Reminder */}
          <section className="bg-surface border border-border rounded-xl shadow-sm overflow-hidden">
            <div className="flex items-center gap-3 p-5 border-b border-border bg-surface-sunk/30">
              <StickyNote size={20} className="text-accent" />
              <h2 className="m-0 text-lg font-semibold tracking-tight">New Note / Reminder</h2>
            </div>
            <form onSubmit={handleCreate} className="p-5 flex flex-col gap-4">
              <input
                type="text"
                placeholder="Title (e.g., Upcoming Interview at XYZ)"
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                className="text-lg font-semibold px-4 py-3 border-transparent bg-neutral-soft focus:bg-surface focus:border-border-strong rounded-lg transition-all"
                required
              />
              <div className="relative">
                <textarea
                  placeholder="Jot down notes, paste updates, use Markdown..."
                  value={newContent}
                  onChange={(e) => setNewContent(e.target.value)}
                  className="w-full min-h-[160px] p-4 pb-12 border-transparent bg-neutral-soft focus:bg-surface focus:border-border-strong rounded-lg transition-all"
                  required
                />
                <div className="absolute bottom-3 left-3 flex gap-2">
                  <input
                    type="file"
                    accept="image/*"
                    className="hidden"
                    ref={fileInputRef}
                    onChange={handleImageUpload}
                  />
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="btn-ghost !px-3 !py-1.5 !min-h-[30px] !text-xs !gap-1.5 text-text-muted hover:text-text-main"
                    title="Upload Image"
                  >
                    <ImageIcon size={14} /> Add Image
                  </button>
                </div>
                <button
                  type="submit"
                  disabled={creating}
                  className="btn absolute bottom-3 right-3 !px-4 !py-1.5 !min-h-[30px] !text-xs"
                >
                  {creating ? <Loader2 size={14} className="animate-spin" /> : "Save Note"}
                </button>
              </div>
            </form>
          </section>

          {/* List Reminders */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {loading ? (
              <div className="col-span-full py-12 text-center text-text-muted">Loading notes...</div>
            ) : reminders.length === 0 ? (
              <div className="col-span-full py-12 text-center text-text-muted">
                No notes yet. Add one above!
              </div>
            ) : (
              reminders.map((r) => (
                <div
                  key={r.id}
                  className="relative group bg-surface border border-border rounded-xl shadow-sm p-6 hover:shadow-md hover:border-border-strong transition-all flex flex-col"
                >
                  <button
                    onClick={() => handleDelete(r.id)}
                    className="absolute top-4 right-4 opacity-0 group-hover:opacity-100 p-1.5 rounded-md text-text-muted hover:bg-danger-soft hover:text-danger transition-all"
                  >
                    <X size={16} />
                  </button>
                  <h3 className="font-bold text-lg mb-2 pr-6 truncate">{r.title}</h3>
                  <div className="text-sm text-text-muted mb-4 prose prose-sm prose-neutral dark:prose-invert max-w-none flex-grow overflow-hidden">
                    <ReactMarkdown>{r.content_text}</ReactMarkdown>
                  </div>
                  <div className="text-xs font-semibold uppercase tracking-wider text-text-subtle pt-4 border-t border-border mt-auto">
                    {new Date(r.created_at).toLocaleDateString()}
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
