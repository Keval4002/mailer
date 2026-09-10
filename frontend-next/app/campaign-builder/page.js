"use client";

import { useState, useEffect, Suspense, useCallback } from "react";
import api from "@/lib/api";
import { Plus, X, Loader2, CheckCircle2, AlertCircle, Send, Users, Mail } from "lucide-react";
import TopNav from "@/app/components/TopNav";
import { useSearchParams } from "next/navigation";

const TEMPLATES = {
  generic_full_time: {
    steps: [
      {
        subject: "Application: Engineering Opportunities at {company} / Keval Ambani",
        body: `Hi {name},

I recently submitted my application for an engineering role at {company} and wanted to reach out directly to introduce myself.

I'm Keval Ambani, a Computer Engineering student at Thapar Institute (2027). I'm reaching out because I've been building and shipping full-stack products with a strong focus on AI, and I'm very interested in the work your team is doing.

To give you a quick sense of my background:
- I built an AI business-automation agent running for 2 businesses.
- I built a timetable platform used by 10,000+ students.
- During my internship at Neural Network Labs, I built AI-powered presentation workflows.
- I've also worked as a freelance developer on a global B2B marketplace and production web projects.

I'm particularly interested in software engineering roles where I can work close to the product and build things end-to-end, whether that's an internship now or a full-time role after I graduate in 2027.

Would you be open to a brief conversation about any upcoming engineering opportunities at {company}?

Best,
Keval Ambani
+91-7439459385
LinkedIn: https://www.linkedin.com/in/keval-ambani-9ba99532a
GitHub: https://github.com/Keval4002`,
      },
      {
        subject: "",
        body: `Hi {name},

Just bumping this up in case it got buried. I know how busy things get!

I'm still very interested in contributing to {company}. As I mentioned in my application, my most recent project is an AI agent I built using LangGraph + FastMCP that's actively running for 2 businesses in India, cutting operational effort by 70%+.

I'm confident I can ship fast and deliver real impact from day one. Would you be open to discussing the opportunities?

Best,
Keval Ambani
+91-7439459385`,
      },
      {
        subject: "",
        body: `Hi {name},

Just following up on my application and previous notes.

I know things might be busy on your end, but I'm still very keen on exploring engineering roles at {company}. I've had the chance to work across the stack—from building AI/agent workflows and backend systems to shipping customer-facing web products.

I'd be glad to go through the standard interview process and demonstrate how my background aligns with your engineering needs.

Would it be possible to connect for an interview opportunity?

Best,
Keval Ambani
+91-7439459385
LinkedIn: https://www.linkedin.com/in/keval-ambani-9ba99532a`,
      },
    ],
  },
};

/* ── Step badge ── */
function StepBadge({ day, label, color }) {
  const colorMap = {
    purple: { bg: "rgba(124,109,255,0.12)", color: "#7c6dff", border: "rgba(124,109,255,0.25)", glow: "0 0 8px rgba(124,109,255,0.3)" },
    blue:   { bg: "rgba(59,158,255,0.12)",  color: "#3b9eff", border: "rgba(59,158,255,0.25)",  glow: "0 0 8px rgba(59,158,255,0.3)" },
    muted:  { bg: "var(--neutral-soft)",    color: "var(--text-subtle)", border: "var(--border)", glow: "none" },
  };
  const s = colorMap[color] || colorMap.muted;
  return (
    <span
      className="text-[10px] font-bold px-2.5 py-1 rounded-full uppercase tracking-wider"
      style={{ background: s.bg, color: s.color, border: `1px solid ${s.border}`, boxShadow: s.glow }}
    >
      {day}
    </span>
  );
}

function CampaignBuilderContent() {
  const searchParams = useSearchParams();

  // ── helper: format a Date to datetime-local string (local time) ──
  const toLocalDT = (d) => {
    const pad = n => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };

  // ── default: tomorrow at 09:00 ──
  const defaultDate = () => {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(9, 0, 0, 0);
    return toLocalDT(d);
  };

  const DATE_PRESETS = [
    {
      id: "tonight",
      label: "Tonight 7 pm",
      compute: () => { const d = new Date(); d.setHours(19,0,0,0); return toLocalDT(d); },
    },
    {
      id: "tomorrow",
      label: "Tomorrow 9 am",
      compute: () => { const d = new Date(); d.setDate(d.getDate()+1); d.setHours(9,0,0,0); return toLocalDT(d); },
    },
    {
      id: "dayafter",
      label: "Day after 9 am",
      compute: () => { const d = new Date(); d.setDate(d.getDate()+2); d.setHours(9,0,0,0); return toLocalDT(d); },
    },
    { id: "custom", label: "Custom…", compute: null },
  ];

  const [contacts,        setContacts]        = useState([]);
  const [includeFollowups,setIncludeFollowups]= useState(true);
  const [attachResume,    setAttachResume]    = useState(true);
  const [startDate,       setStartDate]       = useState(defaultDate);
  const [activePreset,    setActivePreset]    = useState("tomorrow");
  const [sequence,        setSequence]        = useState([
    { subject: TEMPLATES.generic_full_time.steps[0].subject, body: TEMPLATES.generic_full_time.steps[0].body },
    { subject: TEMPLATES.generic_full_time.steps[1].subject, body: TEMPLATES.generic_full_time.steps[1].body },
    { subject: TEMPLATES.generic_full_time.steps[2].subject, body: TEMPLATES.generic_full_time.steps[2].body },
  ]);
  const [loading, setLoading] = useState(false);
  const [result,  setResult]  = useState(null);

  const applyPreset = (preset) => {
    setActivePreset(preset.id);
    if (preset.compute) setStartDate(preset.compute());
  };

  const [bulkText, setBulkText] = useState("");
  const [showBulk, setShowBulk] = useState(false);
  const [previewMode, setPreviewMode] = useState(false);

  const renderPreview = (text) => {
    if (!previewMode) return text;
    const c = contacts.find(x => x.email) || contacts[0] || { name: "John", company: "Acme Corp" };
    let n = (c.name || "").split(" ")[0];
    if (!n) n = "there";
    let comp = c.company || "your team";
    return text.replace(/{name}/g, n).replace(/{company}/g, comp);
  };

  const handleBulkImport = () => {
    if (!bulkText.trim()) {
      setShowBulk(false);
      return;
    }
    const lines = bulkText.split('\n').map(l => l.trim()).filter(Boolean);
    const newContacts = [];
    for (const line of lines) {
      const parts = line.split(/\t/).map(p => p.trim());
      if (parts.length > 1) {
        let email = "", name = "", company = "", title = "";
        parts.forEach(p => {
          if (p.includes("@") && p.includes(".")) email = p;
          else if (!name && p.length < 30) name = p;
          else if (!title && p.toLowerCase().match(/(engineer|manager|director|head|talent|recruiter)/)) title = p;
          else if (!company && p.length < 40) company = p;
        });
        if (email || name) newContacts.push({ email, name, company, title });
      } else {
        const cparts = line.split(',').map(p => p.trim());
        if (cparts.length > 1) {
          let email = "", name = "", company = "", title = "";
          cparts.forEach(p => {
            if (p.includes("@") && p.includes(".")) email = p;
            else if (!name && p.length < 30) name = p;
            else if (!title && p.toLowerCase().match(/(engineer|manager|director|head|talent|recruiter)/)) title = p;
            else if (!company && p.length < 40) company = p;
          });
          if (email || name) newContacts.push({ email, name, company, title });
        } else {
          if (line.includes("@")) newContacts.push({ email: line, name: "", company: "", title: "" });
          else newContacts.push({ email: "", name: line, company: "", title: "" });
        }
      }
    }
    if (newContacts.length > 0) {
      // Avoid overwriting if they opened the URL with a specific contact query param
      if (contacts.length === 1 && !contacts[0].email && !contacts[0].name && !contacts[0].company) {
        setContacts(newContacts);
      } else {
        setContacts([...contacts, ...newContacts]);
      }
    }
    setBulkText("");
    setShowBulk(false);
  };

  useEffect(() => {
    const company  = searchParams.get("company");
    const jobAppId = searchParams.get("job_application_id");
    if (company || jobAppId) {
      setContacts([{ email: "", name: "", company: company || "", title: "", job_application_id: jobAppId || undefined }]);
    } else {
      setContacts([{ email: "", name: "", company: "", title: "" }]);
    }
  }, [searchParams]);

  const addContact    = () => setContacts([...contacts, { email: "", name: "", company: "", title: "" }]);
  const removeContact = (i) => setContacts(contacts.filter((_, idx) => idx !== i));
  const updateContact = (i, field, value) => {
    const c = [...contacts]; c[i][field] = value; setContacts(c);
  };
  const updateStep = (i, field, value) => {
    const s = [...sequence]; s[i][field] = value; setSequence(s);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setResult(null);
    const validContacts = contacts.filter(c => c.email && c.email.includes("@"));
    if (validContacts.length === 0) {
      setResult({ type: "error", message: "Please add at least one valid contact." });
      return;
    }
    if (!startDate) {
      setResult({ type: "error", message: "Send date is required." });
      return;
    }
    // Validate send time is not in the past (allow 1-min buffer)
    if (new Date(startDate) < new Date(Date.now() - 60000)) {
      setResult({ type: "error", message: "Send date cannot be in the past." });
      return;
    }
    setLoading(true);
    try {
      const formData = new FormData();
      formData.append("contacts_json", JSON.stringify(validContacts));
      formData.append("start_date", startDate);
      if (includeFollowups) formData.append("include_followups", "true");
      if (attachResume)    formData.append("attach_resume", "true");
      formData.append("subject_0", sequence[0].subject);
      formData.append("body_0",    sequence[0].body);
      formData.append("subject_1", sequence[1].subject);
      formData.append("body_1",    sequence[1].body);
      formData.append("subject_2", sequence[2].subject);
      formData.append("body_2",    sequence[2].body);

      const response = await api.post("/jobs/bulk", formData, { headers: { Accept: "application/json" } });
      if (response.data?.success) {
        setResult({ type: "success", data: response.data.success });
        setContacts([]);
      } else if (response.data?.errors) {
        setResult({ type: "error", message: response.data.errors.join(", ") });
      } else {
        setResult({ type: "success", data: { contacts: validContacts.length, jobs: validContacts.length * (includeFollowups ? 3 : 1) } });
        setContacts([]);
      }
    } catch (err) {
      setResult({ type: "error", message: err.message });
    }
    setLoading(false);
  };

  return (
    <div className="w-[min(1100px,calc(100vw-32px))] mx-auto pb-20">
      <TopNav />
      <main>
        {/* Page header */}
        <div className="mb-8">
          <h1 className="text-3xl font-extrabold tracking-tight flex items-center gap-3"
            style={{ background: "linear-gradient(120deg, var(--text-main) 0%, #7c6dff 100%)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
            <Send size={24} className="text-[#7c6dff]" style={{ WebkitTextFillColor: "initial" }} />
            Campaign Builder
          </h1>
          <p className="text-text-muted mt-2 text-sm">Build and schedule personalized email sequences for multiple contacts at once.</p>
        </div>

        {/* Alerts */}
        {result?.type === "success" && (
          <div className="flex items-start gap-3 p-4 mb-6 rounded-xl border text-sm"
            style={{ background: "var(--success-soft)", borderColor: "rgba(46,170,101,0.25)" }}>
            <CheckCircle2 size={18} className="text-success shrink-0 mt-0.5" />
            <span className="text-text-main">
              Scheduled <b>{result.data.jobs}</b> emails for <b>{result.data.contacts}</b> contacts.
            </span>
          </div>
        )}
        {result?.type === "error" && (
          <div className="flex items-start gap-3 p-4 mb-6 rounded-xl border text-sm"
            style={{ background: "var(--danger-soft)", borderColor: "rgba(224,80,80,0.25)" }}>
            <AlertCircle size={18} className="text-danger shrink-0 mt-0.5" />
            <span className="text-text-main"><b>Error:</b> {result.message}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="grid grid-cols-1 md:grid-cols-[380px_1fr] gap-6 items-start">
          {/* ── LEFT: Contacts ── */}
          <section className="card overflow-hidden">
            <div className="card-glow" />
            <div className="flex items-start justify-between gap-3 border-b border-border p-5">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-[rgba(124,109,255,0.1)] flex items-center justify-center text-[#7c6dff] shrink-0">
                  <Users size={16} />
                </div>
                <div>
                  <h2 className="m-0 text-base font-bold text-text-main">Contacts</h2>
                  <p className="text-text-subtle text-xs mt-0.5">Who will receive this campaign.</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button type="button" onClick={() => setShowBulk(!showBulk)} className="btn-ghost !px-3 !py-1.5 !text-xs !min-h-[30px] shrink-0">
                  Bulk Paste
                </button>
                <button type="button" onClick={addContact} className="btn-ghost !px-3 !py-1.5 !text-xs !min-h-[30px] shrink-0">
                  <Plus size={13} /> Row
                </button>
              </div>
            </div>

            {showBulk && (
              <div className="p-4 border-b border-border bg-neutral-soft">
                <textarea
                  className="w-full h-32 text-xs font-mono p-3 rounded-lg border border-border bg-surface"
                  placeholder="Paste rows from Excel, Sheets, or LinkedIn here...&#10;Format: Name [tab] Title [tab] Company [tab] Email"
                  value={bulkText}
                  onChange={e => setBulkText(e.target.value)}
                />
                <div className="flex justify-end gap-2 mt-2">
                  <button type="button" onClick={() => setShowBulk(false)} className="btn-ghost !px-3 !py-1.5 !text-xs">Cancel</button>
                  <button type="button" onClick={handleBulkImport} className="btn-primary !px-3 !py-1.5 !text-xs">Import</button>
                </div>
              </div>
            )}

            <div className="px-5 max-h-[600px] overflow-y-auto">
              {contacts.length === 0 ? (
                <div className="text-text-subtle text-sm text-center py-10">No contacts yet. Click <b>Bulk Paste</b> or <b>+ Row</b> to begin.</div>
              ) : (
                <>
                  {contacts.map((c, i) => (
                    <div key={i} className="group relative grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-2 py-4 border-b border-border">
                      <div className="col-span-2 flex items-center justify-between mb-0.5">
                        <span className="text-[10px] font-bold uppercase tracking-widest text-text-subtle">Contact {i + 1}</span>
                        <button type="button" onClick={() => removeContact(i)} className="w-6 h-6 rounded-md flex items-center justify-center text-text-muted hover:bg-danger-soft hover:text-danger opacity-0 group-hover:opacity-100 transition-all">
                          <X size={13} />
                        </button>
                      </div>
                      <input type="email" placeholder="Email *" required className="col-span-2" value={c.email} onChange={e => updateContact(i, "email", e.target.value)} />
                      <input type="text" placeholder="First Name" value={c.name} onChange={e => updateContact(i, "name", e.target.value)} />
                      <input type="text" placeholder="Company" value={c.company} onChange={e => updateContact(i, "company", e.target.value)} />
                      <input type="text" placeholder="Title" className="col-span-2" value={c.title} onChange={e => updateContact(i, "title", e.target.value)} />
                    </div>
                  ))}
                  <div className="py-4 flex justify-center">
                    <button type="button" onClick={addContact} className="btn-ghost !px-4 !py-2 !text-xs">
                      <Plus size={14} className="mr-1.5" /> Add Row
                    </button>
                  </div>
                </>
              )}
            </div>

            <div className="p-5 border-t border-border">
              <label className="flex items-center gap-2.5 cursor-pointer text-sm font-medium text-text-muted hover:text-text-main transition-colors">
                <div className="relative">
                  <input type="checkbox" className="sr-only peer" checked={attachResume} onChange={e => setAttachResume(e.target.checked)} />
                  <div className="w-9 h-5 bg-neutral-soft rounded-full border border-border peer-checked:border-[#7c6dff] peer-checked:bg-[rgba(124,109,255,0.2)] transition-all" />
                  <div className="absolute top-0.5 left-0.5 w-4 h-4 bg-text-subtle rounded-full peer-checked:translate-x-4 peer-checked:bg-[#7c6dff] transition-all" />
                </div>
                Attach my resume to all emails
              </label>
            </div>
          </section>

          {/* ── RIGHT: Sequence ── */}
          <section className="card overflow-hidden">
            <div className="card-glow" />
            <div className="flex items-start justify-between gap-3 border-b border-border p-5">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-[rgba(124,109,255,0.1)] flex items-center justify-center text-[#7c6dff] shrink-0">
                  <Mail size={16} />
                </div>
                <div>
                  <h2 className="m-0 text-base font-bold text-text-main">Email Sequence</h2>
                  <p className="text-text-subtle text-xs mt-0.5">
                    <code className="font-mono text-[#7c6dff]">{"{name}"}</code> and <code className="font-mono text-[#7c6dff]">{"{company}"}</code> are personalized per contact.
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-4 shrink-0">
                <label className="flex items-center gap-2 text-xs font-semibold text-text-main cursor-pointer">
                  <div className="relative">
                    <input type="checkbox" className="sr-only peer" checked={previewMode} onChange={e => setPreviewMode(e.target.checked)} />
                    <div className="w-8 h-4 bg-neutral-soft rounded-full border border-border peer-checked:border-[#22d3ee] peer-checked:bg-[rgba(34,211,238,0.2)] transition-all" />
                    <div className="absolute top-0.5 left-0.5 w-3 h-3 bg-text-subtle rounded-full peer-checked:translate-x-4 peer-checked:bg-[#22d3ee] transition-all" />
                  </div>
                  Live Preview
                </label>
                <div className="w-px h-4 bg-border" />
                <label className="flex items-center gap-2 text-xs text-text-muted cursor-pointer">
                  <div className="relative">
                    <input type="checkbox" className="sr-only peer" checked={includeFollowups} onChange={e => setIncludeFollowups(e.target.checked)} />
                    <div className="w-8 h-4 bg-neutral-soft rounded-full border border-border peer-checked:border-[#7c6dff] peer-checked:bg-[rgba(124,109,255,0.2)] transition-all" />
                    <div className="absolute top-0.5 left-0.5 w-3 h-3 bg-text-subtle rounded-full peer-checked:translate-x-4 peer-checked:bg-[#7c6dff] transition-all" />
                  </div>
                  Follow-ups
                </label>
              </div>
            </div>

            <div className="p-5 flex flex-col gap-0">
              {/* Step 0 */}
              <div className="relative grid grid-cols-[20px_1fr] gap-5 after:content-[''] after:absolute after:left-[9px] after:top-[24px] after:bottom-[-8px] after:w-[2px] after:bg-border">
                <div className="relative z-10 w-5 h-5 rounded-full mt-3.5"
                  style={{ border: "2.5px solid #7c6dff", background: "var(--surface)", boxShadow: "0 0 10px rgba(124,109,255,0.4)" }} />
                <div className="min-w-0 border border-border rounded-xl p-5 mb-6 hover:border-[rgba(124,109,255,0.3)] transition-all"
                  style={{ background: "var(--surface)" }}>
                  <div className="flex items-center flex-wrap gap-3 mb-4">
                    <StepBadge day="Day 0" color="purple" />
                    <span className="font-semibold text-sm text-text-main">First Email</span>
                    <div className="ml-auto flex flex-col gap-1.5 items-end">
                      <span className="text-[10px] font-bold uppercase tracking-widest text-text-subtle">Send at (IST)</span>
                      {/* Quick preset chips */}
                      <div className="flex flex-wrap gap-1.5 justify-end">
                        {DATE_PRESETS.map(p => (
                          <button
                            key={p.id}
                            type="button"
                            onClick={() => applyPreset(p)}
                            className="text-[10px] font-bold px-2.5 py-1 rounded-full border transition-all"
                            style={{
                              background: activePreset === p.id ? "rgba(124,109,255,0.18)" : "var(--neutral-soft)",
                              borderColor: activePreset === p.id ? "rgba(124,109,255,0.5)" : "var(--border)",
                              color: activePreset === p.id ? "#7c6dff" : "var(--text-subtle)",
                              boxShadow: activePreset === p.id ? "0 0 8px rgba(124,109,255,0.25)" : "none",
                            }}
                          >
                            {p.label}
                          </button>
                        ))}
                      </div>
                      {/* Custom datetime picker — shown when Custom is selected */}
                      {activePreset === "custom" && (
                        <input
                          type="datetime-local"
                          required
                          value={startDate}
                          onChange={e => setStartDate(e.target.value)}
                          className="!py-1.5 !text-xs mt-0.5"
                        />
                      )}
                      {activePreset !== "custom" && startDate && (
                        <div className="text-[10px] text-[#7c6dff] font-mono font-bold">
                          {new Date(startDate).toLocaleString('en-US', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}
                        </div>
                      )}
                    </div>
                  </div>
                  <input
                    type="text"
                    required
                    placeholder="Subject"
                    className="mb-3 font-semibold !text-sm !py-2 !px-3"
                    value={sequence[0].subject}
                    onChange={e => updateStep(0, "subject", e.target.value)}
                    style={{ display: previewMode ? 'none' : 'block' }}
                  />
                  {previewMode && (
                    <div className="mb-3 font-semibold text-sm py-2 px-3 border border-transparent bg-neutral-soft rounded-lg text-text-main">
                      {renderPreview(sequence[0].subject)}
                    </div>
                  )}
                  <textarea
                    required
                    placeholder="Email body..."
                    className="min-h-[220px] !text-sm !py-3 !px-3 leading-relaxed"
                    value={sequence[0].body}
                    onChange={e => updateStep(0, "body", e.target.value)}
                    style={{ display: previewMode ? 'none' : 'block' }}
                  />
                  {previewMode && (
                    <div className="min-h-[220px] text-sm py-3 px-3 border border-transparent bg-neutral-soft rounded-lg text-text-main whitespace-pre-wrap leading-relaxed">
                      {renderPreview(sequence[0].body)}
                    </div>
                  )}
                </div>
              </div>

              {/* Step 1 */}
              {includeFollowups && (
                <div className="relative grid grid-cols-[20px_1fr] gap-5 after:content-[''] after:absolute after:left-[9px] after:top-[24px] after:bottom-[-8px] after:w-[2px] after:bg-border">
                  <div className="relative z-10 w-5 h-5 rounded-full mt-3.5"
                    style={{ border: "2.5px solid #3b9eff", background: "var(--surface)", boxShadow: "0 0 10px rgba(59,158,255,0.4)" }} />
                  <div className="min-w-0 border border-border rounded-xl p-5 mb-6 hover:border-[rgba(59,158,255,0.3)] transition-all"
                    style={{ background: "var(--surface)" }}>
                    <div className="flex items-center gap-3 mb-4">
                      <StepBadge day="Day 3" color="blue" />
                      <span className="font-semibold text-sm text-text-main">Follow-up 1</span>
                    </div>
                    <input
                      type="text"
                      placeholder="Subject (leave blank to reply in same thread)"
                      className="mb-3 font-semibold !text-sm !py-2 !px-3"
                      value={sequence[1].subject}
                      onChange={e => updateStep(1, "subject", e.target.value)}
                      style={{ display: previewMode ? 'none' : 'block' }}
                    />
                    {previewMode && sequence[1].subject && (
                      <div className="mb-3 font-semibold text-sm py-2 px-3 border border-transparent bg-neutral-soft rounded-lg text-text-main">
                        {renderPreview(sequence[1].subject)}
                      </div>
                    )}
                    {previewMode && !sequence[1].subject && (
                      <div className="mb-3 font-semibold text-sm py-2 px-3 border border-transparent bg-[rgba(34,211,238,0.1)] rounded-lg text-[#22d3ee]">
                        ↳ Replies to previous thread
                      </div>
                    )}
                    <textarea
                      required
                      placeholder="Email body..."
                      className="min-h-[160px] !text-sm !py-3 !px-3 leading-relaxed"
                      value={sequence[1].body}
                      onChange={e => updateStep(1, "body", e.target.value)}
                      style={{ display: previewMode ? 'none' : 'block' }}
                    />
                    {previewMode && (
                      <div className="min-h-[160px] text-sm py-3 px-3 border border-transparent bg-neutral-soft rounded-lg text-text-main whitespace-pre-wrap leading-relaxed">
                        {renderPreview(sequence[1].body)}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Step 2 */}
              {includeFollowups && (
                <div className="relative grid grid-cols-[20px_1fr] gap-5">
                  <div className="relative z-10 w-5 h-5 rounded-full mt-3.5"
                    style={{ border: "2.5px solid var(--border-strong)", background: "var(--surface)" }} />
                  <div className="min-w-0 border border-border rounded-xl p-5 mb-6 hover:border-border-strong transition-all"
                    style={{ background: "var(--surface)" }}>
                    <div className="flex items-center flex-wrap gap-3 mb-4">
                      <StepBadge day="Day 8" color="muted" />
                      <span className="font-semibold text-sm text-text-main">Follow-up 2 <span className="text-text-subtle font-normal">(Breakup)</span></span>
                    </div>
                    <input
                      type="text"
                      placeholder="Subject (leave blank to reply in same thread)"
                      className="mb-3 font-semibold !text-sm !py-2 !px-3"
                      value={sequence[2].subject}
                      onChange={e => updateStep(2, "subject", e.target.value)}
                      style={{ display: previewMode ? 'none' : 'block' }}
                    />
                    {previewMode && sequence[2].subject && (
                      <div className="mb-3 font-semibold text-sm py-2 px-3 border border-transparent bg-neutral-soft rounded-lg text-text-main">
                        {renderPreview(sequence[2].subject)}
                      </div>
                    )}
                    {previewMode && !sequence[2].subject && (
                      <div className="mb-3 font-semibold text-sm py-2 px-3 border border-transparent bg-[rgba(34,211,238,0.1)] rounded-lg text-[#22d3ee]">
                        ↳ Replies to previous thread
                      </div>
                    )}
                    <textarea
                      required
                      placeholder="Email body..."
                      className="min-h-[160px] !text-sm !py-3 !px-3 leading-relaxed"
                      value={sequence[2].body}
                      onChange={e => updateStep(2, "body", e.target.value)}
                      style={{ display: previewMode ? 'none' : 'block' }}
                    />
                    {previewMode && (
                      <div className="min-h-[160px] text-sm py-3 px-3 border border-transparent bg-neutral-soft rounded-lg text-text-main whitespace-pre-wrap leading-relaxed">
                        {renderPreview(sequence[2].body)}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>

            <div className="flex items-center justify-between gap-3 p-5 border-t border-border" style={{ background: "var(--surface-sunk)" }}>
              <span className="text-xs text-text-subtle">
                {contacts.filter(c => c.email?.includes("@")).length} valid contact{contacts.filter(c => c.email?.includes("@")).length !== 1 ? "s" : ""} · {includeFollowups ? "3" : "1"} email{includeFollowups ? "s" : ""} each
              </span>
              <button type="submit" disabled={loading} className="btn px-6">
                {loading ? <><Loader2 size={15} className="animate-spin" /> Scheduling…</> : <><Send size={14} /> Schedule Campaign</>}
              </button>
            </div>
          </section>
        </form>
      </main>
    </div>
  );
}

export default function CampaignBuilderPage() {
  return (
    <Suspense fallback={<div className="p-12 text-center text-text-muted">Loading builder…</div>}>
      <CampaignBuilderContent />
    </Suspense>
  );
}
