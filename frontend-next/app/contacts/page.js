"use client";

import { useEffect, useState } from "react";
import api from "@/lib/api";
import { Users, Mail, Building2, Hash, Search } from "lucide-react";
import TopNav from "@/app/components/TopNav";

function Avatar({ name, email }) {
  const initials = name
    ? name.split(" ").map(w => w[0]).slice(0, 2).join("").toUpperCase()
    : email?.[0]?.toUpperCase() || "?";

  // Deterministic pastel from string
  const hue = [...(name || email || "")].reduce((acc, c) => acc + c.charCodeAt(0), 0) % 360;

  return (
    <div
      className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold shrink-0"
      style={{
        background: `hsl(${hue}, 55%, 25%)`,
        color: `hsl(${hue}, 70%, 70%)`,
        border: `1px solid hsl(${hue}, 40%, 35%)`,
      }}
    >
      {initials}
    </div>
  );
}

export default function ContactsPage() {
  const [contacts, setContacts] = useState([]);
  const [loading,  setLoading]  = useState(true);
  const [search,   setSearch]   = useState("");

  useEffect(() => {
    api
      .get("/api/contacts")
      .then(res => {
        setContacts(res.data.contacts || []);
        setLoading(false);
      })
      .catch(err => {
        console.error(err);
        setLoading(false);
      });
  }, []);

  const filtered = contacts.filter(c =>
    !search ||
    c.recipient_name?.toLowerCase().includes(search.toLowerCase()) ||
    c.recipient_email?.toLowerCase().includes(search.toLowerCase()) ||
    c.company?.toLowerCase().includes(search.toLowerCase())
  );

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
              Contact Directory
            </h1>
            <p className="text-text-muted mt-2 text-sm">Everyone you've reached out to.</p>
          </div>
          {!loading && contacts.length > 0 && (
            <div className="text-right shrink-0">
              <div className="text-2xl font-bold text-text-main">{contacts.length}</div>
              <div className="text-[10px] font-bold uppercase tracking-widest text-text-subtle">Total Contacts</div>
            </div>
          )}
        </div>

        <div className="card overflow-hidden">
          <div className="card-glow" />

          {/* Search bar */}
          <div className="p-4 border-b border-border flex items-center gap-3">
            <Search size={15} className="text-text-subtle shrink-0" />
            <input
              type="search"
              placeholder="Search by name, email or company…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="!border-0 !bg-transparent !shadow-none !p-0 !rounded-none text-sm placeholder:text-text-subtle"
            />
          </div>

          {loading ? (
            <div className="p-16 text-center text-text-muted flex flex-col items-center gap-3">
              <div className="w-7 h-7 rounded-full border-2 border-[rgba(124,109,255,0.2)] border-t-[#7c6dff] animate-spin" />
              Loading contacts…
            </div>
          ) : filtered.length === 0 ? (
            <div className="p-16 text-center flex flex-col items-center gap-3">
              <div className="w-14 h-14 rounded-2xl bg-[rgba(124,109,255,0.08)] flex items-center justify-center text-[#7c6dff]">
                <Users size={28} />
              </div>
              <p className="text-text-muted text-sm">
                {search ? "No contacts match your search." : "No contacts yet. Send a campaign to add contacts!"}
              </p>
            </div>
          ) : (
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-border" style={{ background: "var(--surface-sunk)" }}>
                  {[
                    { icon: Users,    label: "Name" },
                    { icon: Mail,     label: "Email" },
                    { icon: Building2,label: "Company" },
                    { icon: Hash,     label: "Sent", right: true },
                  ].map(({ icon: Icon, label, right }) => (
                    <th key={label} className={`px-5 py-3.5 ${right ? "text-right" : ""}`}>
                      <span className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest text-text-subtle">
                        <Icon size={11} />
                        {label}
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map((c) => (
                  <tr
                    key={c.recipient_email}
                    className="border-b border-border last:border-0 transition-colors group"
                    style={{ cursor: "default" }}
                    onMouseEnter={e => e.currentTarget.style.background = "var(--neutral-soft)"}
                    onMouseLeave={e => e.currentTarget.style.background = ""}
                  >
                    <td className="px-5 py-3.5">
                      <div className="flex items-center gap-3">
                        <Avatar name={c.recipient_name} email={c.recipient_email} />
                        <span className="font-semibold text-sm text-text-main">{c.recipient_name || "—"}</span>
                      </div>
                    </td>
                    <td className="px-5 py-3.5 text-sm text-text-muted font-mono text-xs">{c.recipient_email}</td>
                    <td className="px-5 py-3.5 text-sm text-text-muted">{c.company || "—"}</td>
                    <td className="px-5 py-3.5 text-right">
                      <span className="inline-flex items-center justify-center w-7 h-7 rounded-lg text-xs font-bold bg-[rgba(124,109,255,0.08)] text-[#7c6dff] border border-[rgba(124,109,255,0.15)]">
                        {c.sent_mails_count || 0}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </main>
    </div>
  );
}
