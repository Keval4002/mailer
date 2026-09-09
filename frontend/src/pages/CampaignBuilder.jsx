import React, { useState } from 'react';
import api from '../api';
import { Plus, X, Loader2, CheckCircle2, AlertCircle } from 'lucide-react';

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
+91-7439459385`
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
+91-7439459385`
      },
      {
        subject: "",
        body: `Hi {name},

I'll keep this short. I figure now probably isn't the right time, and I don't want to clog your inbox.

I'll stop following up here. But if you're ever looking for an engineer who can move fast, ship real products, and build intelligently with modern AI tools, feel free to reach back out. I'd love to be useful to your team when the timing is right.

Thanks for your time.

Keval Ambani
LinkedIn: https://www.linkedin.com/in/keval-ambani-9ba99532a | GitHub: https://github.com/Keval4002`
      }
    ]
  }
};

export default function CampaignBuilder() {
  const [contacts, setContacts] = useState([]);
  const [includeFollowups, setIncludeFollowups] = useState(true);
  const [attachResume, setAttachResume] = useState(true);
  const [startDate, setStartDate] = useState('');
  
  const [sequence, setSequence] = useState([
    { subject: TEMPLATES.generic_full_time.steps[0].subject, body: TEMPLATES.generic_full_time.steps[0].body },
    { subject: TEMPLATES.generic_full_time.steps[1].subject, body: TEMPLATES.generic_full_time.steps[1].body },
    { subject: TEMPLATES.generic_full_time.steps[2].subject, body: TEMPLATES.generic_full_time.steps[2].body }
  ]);

  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  const addContact = () => {
    setContacts([...contacts, { email: '', name: '', company: '', title: '' }]);
  };

  const removeContact = (index) => {
    setContacts(contacts.filter((_, i) => i !== index));
  };

  const updateContact = (index, field, value) => {
    const newContacts = [...contacts];
    newContacts[index][field] = value;
    setContacts(newContacts);
  };

  const updateStep = (index, field, value) => {
    const newSeq = [...sequence];
    newSeq[index][field] = value;
    setSequence(newSeq);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setResult(null);

    const validContacts = contacts.filter(c => c.email && c.email.includes('@'));
    if (validContacts.length === 0) {
      setResult({ type: 'error', message: 'Please add at least one valid contact.' });
      return;
    }
    if (!startDate) {
      setResult({ type: 'error', message: 'Send date is required.' });
      return;
    }

    setLoading(true);
    try {
      const formData = new FormData();
      formData.append('contacts_json', JSON.stringify(validContacts));
      formData.append('start_date', startDate);
      if (includeFollowups) formData.append('include_followups', 'true');
      if (attachResume) formData.append('attach_resume', 'true');

      formData.append('subject_0', sequence[0].subject);
      formData.append('body_0', sequence[0].body);
      formData.append('subject_1', sequence[1].subject);
      formData.append('body_1', sequence[1].body);
      formData.append('subject_2', sequence[2].subject);
      formData.append('body_2', sequence[2].body);

      // We post to the backend endpoint. Currently it returns HTML from the template.
      // We will assume it succeeds if status is 200, though a true REST API would be better.
      // We'll update the backend to support JSON response if Accept: application/json is sent.
      const response = await api.post('/jobs/bulk', formData, {
        headers: { 'Accept': 'application/json' }
      });

      if (response.data && response.data.success) {
         setResult({ type: 'success', data: response.data.success });
         setContacts([]);
      } else if (response.data && response.data.errors) {
         setResult({ type: 'error', message: response.data.errors.join(', ') });
      } else {
         // Fallback if the backend still returned HTML
         setResult({ type: 'success', data: { contacts: validContacts.length, jobs: validContacts.length * (includeFollowups ? 3 : 1) } });
         setContacts([]);
      }
    } catch (err) {
      setResult({ type: 'error', message: err.message });
    }
    setLoading(false);
  };

  return (
    <div>
      {result?.type === 'success' && (
        <div className="flex items-start gap-3 p-4 mb-6 rounded-md bg-success-soft border-l-4 border-success text-text-main">
          <CheckCircle2 className="text-success shrink-0" size={20} />
          <span>Scheduled <b>{result.data.jobs}</b> emails for <b>{result.data.contacts}</b> contacts.</span>
        </div>
      )}
      {result?.type === 'error' && (
        <div className="flex items-start gap-3 p-4 mb-6 rounded-md bg-danger-soft border-l-4 border-danger text-text-main">
          <AlertCircle className="text-danger shrink-0" size={20} />
          <span><b>Errors:</b> {result.message}</span>
        </div>
      )}

      <form onSubmit={handleSubmit} className="grid grid-cols-1 md:grid-cols-[360px_1fr] gap-6 items-start">
        {/* LEFT: Contacts */}
        <section className="bg-surface border border-border rounded-xl shadow-sm overflow-hidden">
          <div className="flex items-start justify-between gap-3 border-b border-border p-5 pb-4">
            <div>
              <h2 className="m-0 text-lg font-semibold tracking-tight">Contacts</h2>
              <p className="text-text-muted text-sm mt-1">People who will receive this campaign.</p>
            </div>
            <button type="button" onClick={addContact} className="btn-ghost px-3 py-1.5 text-sm rounded-md">
              <Plus size={16} /> Add
            </button>
          </div>

          <div className="px-5 min-h-[60px]">
            {contacts.length === 0 ? (
              <div className="text-text-muted text-sm text-center py-8">
                No contacts yet. Click "+ Add" to begin.
              </div>
            ) : (
              contacts.map((c, i) => (
                <div key={i} className="group relative grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-2 py-4 border-b border-border last:border-0 transition-colors hover:bg-neutral-soft hover:-mx-5 hover:px-5 hover:rounded-lg">
                  <div className="col-span-1 sm:col-span-2 text-[10px] font-semibold uppercase tracking-wider text-text-muted mb-1">
                    Contact {i + 1}
                  </div>
                  <input
                    type="email"
                    placeholder="Email *"
                    required
                    className="col-span-1 sm:col-span-2"
                    value={c.email}
                    onChange={(e) => updateContact(i, 'email', e.target.value)}
                  />
                  <input
                    type="text"
                    placeholder="First Name"
                    value={c.name}
                    onChange={(e) => updateContact(i, 'name', e.target.value)}
                  />
                  <input
                    type="text"
                    placeholder="Company"
                    value={c.company}
                    onChange={(e) => updateContact(i, 'company', e.target.value)}
                  />
                  <input
                    type="text"
                    placeholder="Title"
                    className="col-span-1 sm:col-span-2"
                    value={c.title}
                    onChange={(e) => updateContact(i, 'title', e.target.value)}
                  />
                  <button
                    type="button"
                    onClick={() => removeContact(i)}
                    className="absolute top-3 right-0 opacity-0 group-hover:opacity-100 group-hover:right-3 p-1 rounded bg-transparent text-text-muted hover:bg-danger-soft hover:text-danger transition-all"
                  >
                    <X size={16} />
                  </button>
                </div>
              ))
            )}
          </div>

          <div className="p-5 pt-0 mt-4 border-t border-border">
            <label className="flex flex-row items-center gap-2 cursor-pointer text-sm font-medium mt-4">
              <input type="checkbox" className="w-4 h-4 accent-accent" checked={attachResume} onChange={(e) => setAttachResume(e.target.checked)} />
              <span>Attach my resume to all emails</span>
            </label>
          </div>
        </section>

        {/* RIGHT: Sequence */}
        <section className="bg-surface border border-border rounded-xl shadow-sm overflow-hidden">
          <div className="flex items-start justify-between gap-3 border-b border-border p-5 pb-4">
            <div>
              <h2 className="m-0 text-lg font-semibold tracking-tight">Email Sequence</h2>
              <p className="text-text-muted text-sm mt-1">Edit the body for each step. <code>{'{name}'}</code> and <code>{'{company}'}</code> will be personalized per contact.</p>
            </div>
            <label className="flex items-center gap-2 text-sm text-text-muted cursor-pointer">
              <span>Include follow-ups</span>
              <input type="checkbox" className="w-4 h-4 accent-accent" checked={includeFollowups} onChange={(e) => setIncludeFollowups(e.target.checked)} />
            </label>
          </div>

          <div className="p-5 px-6 flex flex-col gap-0">
            {/* Step 0 */}
            <div className="relative grid grid-cols-[24px_1fr] gap-5 after:content-[''] after:absolute after:left-[11px] after:top-[28px] after:bottom-[-8px] after:w-[2px] after:bg-border">
              <div className="relative z-10 w-6 h-6 rounded-full border-[3px] border-accent bg-surface shadow-[0_0_0_4px_rgba(0,0,0,0.05)] mt-3.5"></div>
              <div className="min-w-0 bg-surface border border-border rounded-lg p-5 mb-6 shadow-sm hover:shadow-md hover:border-border-strong transition-all">
                <div className="flex items-center flex-wrap gap-3 mb-4">
                  <span className="text-[11px] font-semibold px-2.5 py-1 rounded-full uppercase tracking-wider bg-accent-soft text-accent">Day 0</span>
                  <span className="font-semibold text-sm">First Email</span>
                  <div className="ml-auto flex flex-col gap-1">
                    <span className="text-xs font-medium text-text-muted">Send at (IST)</span>
                    <input type="datetime-local" required value={startDate} onChange={(e) => setStartDate(e.target.value)} className="py-1.5 text-sm" />
                  </div>
                </div>
                <div className="flex flex-col gap-1.5 mb-4 mt-2">
                  <label className="text-xs font-semibold uppercase tracking-wider text-text-subtle">Subject</label>
                  <input type="text" value={sequence[0].subject} onChange={(e) => updateStep(0, 'subject', e.target.value)} className="w-full" />
                </div>
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-semibold uppercase tracking-wider text-text-subtle">Body</label>
                  <textarea value={sequence[0].body} onChange={(e) => updateStep(0, 'body', e.target.value)} rows={10} className="w-full resize-y" />
                </div>
              </div>
            </div>

            {/* Step 1 */}
            {includeFollowups && (
              <div className="relative grid grid-cols-[24px_1fr] gap-5 after:content-[''] after:absolute after:left-[11px] after:top-[28px] after:bottom-[-8px] after:w-[2px] after:bg-border">
                <div className="relative z-10 w-6 h-6 rounded-full border-[3px] border-info bg-surface shadow-[0_0_0_4px_rgba(0,145,255,0.1)] mt-3.5"></div>
                <div className="min-w-0 bg-surface border border-border rounded-lg p-5 mb-6 shadow-sm hover:shadow-md hover:border-border-strong transition-all">
                  <div className="flex items-center flex-wrap gap-3 mb-4">
                    <span className="text-[11px] font-semibold px-2.5 py-1 rounded-full uppercase tracking-wider bg-info-soft text-info">Day 3</span>
                    <span className="font-semibold text-sm">Follow-up 1</span>
                    <span className="text-xs text-text-muted ml-auto">Auto-scheduled 3 days after</span>
                  </div>
                  <div className="flex flex-col gap-1.5 mb-4 mt-2">
                    <label className="text-xs font-semibold uppercase tracking-wider text-text-subtle">Subject <span className="lowercase font-normal normal-case">(leave blank to reply in thread)</span></label>
                    <input type="text" value={sequence[1].subject} onChange={(e) => updateStep(1, 'subject', e.target.value)} className="w-full" />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <label className="text-xs font-semibold uppercase tracking-wider text-text-subtle">Body</label>
                    <textarea value={sequence[1].body} onChange={(e) => updateStep(1, 'body', e.target.value)} rows={8} className="w-full resize-y" />
                  </div>
                </div>
              </div>
            )}

            {/* Step 2 */}
            {includeFollowups && (
              <div className="relative grid grid-cols-[24px_1fr] gap-5">
                <div className="relative z-10 w-6 h-6 rounded-full border-[3px] border-border-strong bg-surface mt-3.5"></div>
                <div className="min-w-0 bg-surface border border-border rounded-lg p-5 mb-6 shadow-sm hover:shadow-md hover:border-border-strong transition-all">
                  <div className="flex items-center flex-wrap gap-3 mb-4">
                    <span className="text-[11px] font-semibold px-2.5 py-1 rounded-full uppercase tracking-wider bg-neutral-soft text-text-muted">Day 8</span>
                    <span className="font-semibold text-sm">Follow-up 2 (Breakup)</span>
                    <span className="text-xs text-text-muted ml-auto">Auto-scheduled 8 days after</span>
                  </div>
                  <div className="flex flex-col gap-1.5 mb-4 mt-2">
                    <label className="text-xs font-semibold uppercase tracking-wider text-text-subtle">Subject <span className="lowercase font-normal normal-case">(leave blank to reply in thread)</span></label>
                    <input type="text" value={sequence[2].subject} onChange={(e) => updateStep(2, 'subject', e.target.value)} className="w-full" />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <label className="text-xs font-semibold uppercase tracking-wider text-text-subtle">Body</label>
                    <textarea value={sequence[2].body} onChange={(e) => updateStep(2, 'body', e.target.value)} rows={8} className="w-full resize-y" />
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="flex items-center justify-end gap-3 p-5 border-t border-border bg-surface-sunk/30">
            <button type="submit" disabled={loading} className="btn px-8">
              {loading ? <><Loader2 size={16} className="animate-spin"/> Scheduling...</> : 'Schedule Campaign'}
            </button>
          </div>
        </section>
      </form>
    </div>
  );
}
