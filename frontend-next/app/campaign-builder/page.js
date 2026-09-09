"use client";

import { useState, useEffect, Suspense } from "react";
import api from "@/lib/api";
import { Plus, X, Loader2, CheckCircle2, AlertCircle, Send, Users, Mail } from "lucide-react";
import TopNav from "@/app/components/TopNav";
import { useSearchParams } from "next/navigation";

const TEMPLATES = {
  generic_full_time: {
    steps: [
      {
        subject: "Exploring Engineering Opportunities at {company}",
        body: `Hi {name},

I hope you're doing well!

I'm Keval Ambani, a Computer Engineering student at Thapar Institute of Engineering and Technology (Batch 2027), and I'm reaching out because I'm genuinely excited about the engineering work happening at {company}.

Some highlights from my recent work:

- Built VyapaarSetu, an AI agent using LangGraph deep agents + FastMCP that automates lead qualification, invoicing, scheduling and order fulfillment for 2 active stores across India, reducing repetitive operational effort by 70%+. Integrated WhatsApp Business, Google Calendar and Razorpay through MCP-powered tool calling.
- At Neural Network Labs (Full Stack Intern), built AI-powered presentation generation workflows using Gemini and async workers, reducing creation time to under 120 seconds and contributing to 20%+ growth in trial adoption.
- Freelanced at GlassFactory building a Tariff Calculator and implementing adaptive bitrate streaming via AWS MediaConvert for a global B2B marketplace.

My stack: React, Next.js, Node.js, FastAPI, LangGraph, Python, PostgreSQL, Docker, and I'm actively building with agentic AI tools.

You can explore my work here:
- GitHub: https://github.com/Keval4002
- LinkedIn: https://www.linkedin.com/in/keval-ambani-9ba99532a

Would you be open to a brief chat about any full-time or internship engineering roles on your team?

Best regards,
Keval Ambani
+91-7439459385`,
      },
      {
        subject: "Re: Exploring Engineering Opportunities at {company}",
        body: `Hi {name},

Just bumping this up in case it got buried. Totally understand how busy things get!

I'm still very interested in contributing to {company}. To give you a quick sense of what I bring:
my most recent project, VyapaarSetu, is an AI agent I built using LangGraph + FastMCP that's actively running for 2 businesses in India, automating their lead qualification, invoicing and scheduling end-to-end and cutting operational effort by 70%+.

I'm confident I can ship fast and deliver real impact from day one.

Would a 15-minute call this week work for you?

Best,
Keval Ambani
+91-7439459385`,
      },
      {
        subject: "",
        body: `Hi {name},

I'll keep this short. I figure now probably isn't the right time, and I don't want to clog your inbox.

I'll stop following up here. But if you're ever looking for an engineer who can move fast, ship real products, and build intelligently with modern AI tools, feel free to reach back out. I'd love to be useful to your team when the timing is right.

Thanks for your time.

Keval Ambani
LinkedIn: https://www.linkedin.com/in/keval-ambani-9ba99532a | GitHub: https://github.com/Keval4002`,
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

  const [contacts,        setContacts]        = useState([]);
  const [includeFollowups,setIncludeFollowups]= useState(true);
  const [attachResume,    setAttachResume]    = useState(true);
  const [startDate,       setStartDate]       = useState("");
  const [sequence,        setSequence]        = useState([
    { subject: TEMPLATES.generic_full_time.steps[0].subject, body: TEMPLATES.generic_full_time.steps[0].body },
    { subject: TEMPLATES.generic_full_time.steps[1].subject, body: TEMPLATES.generic_full_time.steps[1].body },
    { subject: TEMPLATES.generic_full_time.steps[2].subject, body: TEMPLATES.generic_full_time.steps[2].body },
  ]);
  const [loading, setLoading] = useState(false);
  const [result,  setResult]  = useState(null);

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
              <button type="button" onClick={addContact} className="btn-ghost !px-3 !py-1.5 !text-xs !min-h-[30px] shrink-0">
                <Plus size={13} /> Add
              </button>
            </div>

            <div className="px-5">
              {contacts.length === 0 ? (
                <div className="text-text-subtle text-sm text-center py-10">No contacts yet. Click <b>+ Add</b> to begin.</div>
              ) : (
                contacts.map((c, i) => (
                  <div key={i} className="group relative grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-2 py-4 border-b border-border last:border-0">
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
                ))
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
              <label className="flex items-center gap-2 text-xs text-text-muted cursor-pointer shrink-0">
                <div className="relative">
                  <input type="checkbox" className="sr-only peer" checked={includeFollowups} onChange={e => setIncludeFollowups(e.target.checked)} />
                  <div className="w-8 h-4 bg-neutral-soft rounded-full border border-border peer-checked:border-[#7c6dff] peer-checked:bg-[rgba(124,109,255,0.2)] transition-all" />
                  <div className="absolute top-0.5 left-0.5 w-3 h-3 bg-text-subtle rounded-full peer-checked:translate-x-4 peer-checked:bg-[#7c6dff] transition-all" />
                </div>
                Follow-ups
              </label>
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
                    <div className="ml-auto flex flex-col gap-1">
                      <span className="text-xs text-text-subtle">Send at (IST)</span>
                      <input type="datetime-local" required value={startDate} onChange={e => setStartDate(e.target.value)} className="!py-1.5 !text-xs" />
                    </div>
                  </div>
                  <div className="flex flex-col gap-1.5 mb-3">
                    <label className="text-[10px] font-bold uppercase tracking-widest text-text-subtle">Subject</label>
                    <input type="text" value={sequence[0].subject} onChange={e => updateStep(0, "subject", e.target.value)} />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <label className="text-[10px] font-bold uppercase tracking-widest text-text-subtle">Body</label>
                    <textarea value={sequence[0].body} onChange={e => updateStep(0, "body", e.target.value)} rows={10} className="resize-y" />
                  </div>
                </div>
              </div>

              {/* Step 1 */}
              {includeFollowups && (
                <div className="relative grid grid-cols-[20px_1fr] gap-5 after:content-[''] after:absolute after:left-[9px] after:top-[24px] after:bottom-[-8px] after:w-[2px] after:bg-border">
                  <div className="relative z-10 w-5 h-5 rounded-full mt-3.5"
                    style={{ border: "2.5px solid #3b9eff", background: "var(--surface)", boxShadow: "0 0 10px rgba(59,158,255,0.4)" }} />
                  <div className="min-w-0 border border-border rounded-xl p-5 mb-6 hover:border-[rgba(59,158,255,0.3)] transition-all"
                    style={{ background: "var(--surface)" }}>
                    <div className="flex items-center flex-wrap gap-3 mb-4">
                      <StepBadge day="Day 3" color="blue" />
                      <span className="font-semibold text-sm text-text-main">Follow-up 1</span>
                      <span className="text-xs text-text-subtle ml-auto">Auto-scheduled 3 days after</span>
                    </div>
                    <div className="flex flex-col gap-1.5 mb-3">
                      <label className="text-[10px] font-bold uppercase tracking-widest text-text-subtle">Subject <span className="normal-case font-normal">(leave blank to reply in thread)</span></label>
                      <input type="text" value={sequence[1].subject} onChange={e => updateStep(1, "subject", e.target.value)} />
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <label className="text-[10px] font-bold uppercase tracking-widest text-text-subtle">Body</label>
                      <textarea value={sequence[1].body} onChange={e => updateStep(1, "body", e.target.value)} rows={8} className="resize-y" />
                    </div>
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
                      <span className="text-xs text-text-subtle ml-auto">Auto-scheduled 8 days after</span>
                    </div>
                    <div className="flex flex-col gap-1.5 mb-3">
                      <label className="text-[10px] font-bold uppercase tracking-widest text-text-subtle">Subject <span className="normal-case font-normal">(leave blank to reply in thread)</span></label>
                      <input type="text" value={sequence[2].subject} onChange={e => updateStep(2, "subject", e.target.value)} />
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <label className="text-[10px] font-bold uppercase tracking-widest text-text-subtle">Body</label>
                      <textarea value={sequence[2].body} onChange={e => updateStep(2, "body", e.target.value)} rows={8} className="resize-y" />
                    </div>
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
