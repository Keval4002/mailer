"use client";

import { useEffect, useState } from "react";
import api from "@/lib/api";
import { Users, Building2, Search, Mail, Briefcase, ChevronDown, ChevronUp, Phone } from "lucide-react";
import TopNav from "@/app/components/TopNav";

function Avatar({ name, email }) {
  const initials = name
    ? name.split(" ").map(w => w[0]).slice(0, 2).join("").toUpperCase()
    : email?.[0]?.toUpperCase() || "?";
  const hue = [...(name || email || "")].reduce((a, c) => a + c.charCodeAt(0), 0) % 360;
  return (
    <div className="w-9 h-9 rounded-full flex items-center justify-center text-xs font-bold shrink-0"
      style={{ background: `hsl(${hue},55%,22%)`, color: `hsl(${hue},70%,70%)`, border: `1px solid hsl(${hue},40%,35%)` }}>
      {initials}
    </div>
  );
}

export default function ContactsPage() {
  const [contacts, setContacts] = useState([]);
  const [loading,  setLoading]  = useState(true);
  const [search,   setSearch]   = useState("");
  const [expandedCompanies, setExpandedCompanies] = useState({});

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
    !search ||
    c.name?.toLowerCase().includes(search.toLowerCase()) ||
    c.email?.toLowerCase().includes(search.toLowerCase()) ||
    c.company?.toLowerCase().includes(search.toLowerCase())
  );

  // Group by company
  const groupedContacts = filtered.reduce((acc, c) => {
    const company = c.company ? c.company.trim() : "No Company";
    if (!acc[company]) acc[company] = [];
    acc[company].push(c);
    return acc;
  }, {});

  // Sort companies alphabetically (put "No Company" at the end)
  const sortedCompanies = Object.keys(groupedContacts).sort((a, b) => {
    if (a === "No Company") return 1;
    if (b === "No Company") return -1;
    return a.localeCompare(b);
  });

  const toggleCompany = (companyName) => {
    setExpandedCompanies(prev => ({
      ...prev,
      [companyName]: !prev[companyName]
    }));
  };

  // If there's an active search, default to expanding everything that matches
  const isExpanded = (companyName) => {
    if (search.trim()) return true;
    return !!expandedCompanies[companyName];
  };

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
            <p className="text-text-muted mt-2 text-sm">A directory of names and emails, grouped by company.</p>
          </div>
          {!loading && contacts.length > 0 && (
            <div className="text-right shrink-0">
              <div className="text-2xl font-bold text-text-main">{contacts.length}</div>
              <div className="text-[10px] font-bold uppercase tracking-widest text-text-subtle">Total Contacts</div>
            </div>
          )}
        </div>

        {/* Search */}
        <div className="card overflow-hidden mb-6">
          <div className="card-glow" />
          <div className="p-4 flex items-center gap-3">
            <Search size={15} className="text-text-subtle shrink-0" />
            <input
              type="search"
              placeholder="Search by name, email or company…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="!border-0 !bg-transparent !shadow-none !p-0 !rounded-none text-sm placeholder:text-text-subtle w-full"
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
            Loading directory…
          </div>
        ) : filtered.length === 0 ? (
          <div className="card p-16 text-center flex flex-col items-center gap-3">
            <div className="card-glow" />
            <div className="w-14 h-14 rounded-2xl bg-[rgba(124,109,255,0.08)] flex items-center justify-center text-[#7c6dff]">
              <Users size={28} />
            </div>
            <p className="text-text-muted text-sm">
              {search ? "No contacts match your search." : "No contacts yet."}
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            {sortedCompanies.map((companyName) => {
              const expanded = isExpanded(companyName);
              return (
                <div key={companyName} className="card overflow-hidden transition-all" style={{ background: "var(--surface)" }}>
                  <div className="card-glow" />
                  <button 
                    onClick={() => toggleCompany(companyName)}
                    className="w-full text-left px-5 py-4 flex items-center gap-3 hover:bg-[var(--neutral-soft)] transition-colors focus:outline-none"
                  >
                    <Building2 size={16} className="text-[#7c6dff] shrink-0" />
                    <h2 className="text-sm font-bold text-text-main flex-1 truncate">{companyName}</h2>
                    <span className="text-[10px] font-bold text-text-subtle bg-[var(--neutral-soft)] px-2 py-0.5 rounded-full shrink-0">
                      {groupedContacts[companyName].length} contact{groupedContacts[companyName].length !== 1 ? "s" : ""}
                    </span>
                    <div className="text-text-subtle shrink-0 ml-2">
                      {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                    </div>
                  </button>
                  
                  {expanded && (
                    <div className="p-5 pt-2 grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4 bg-[var(--surface-sunk)] border-t border-border">
                      {groupedContacts[companyName].map((c) => (
                        <div key={c.id} className="group relative bg-[var(--surface)] border border-border rounded-xl p-4 hover:border-[#7c6dff]/50 hover:shadow-md transition-all flex flex-col gap-3">
                          <div className="flex items-start justify-between gap-2">
                            <Avatar name={c.name} email={c.email} />
                            <a href={`mailto:${c.email}`} title={`Email ${c.email}`} className="w-8 h-8 rounded-lg bg-[var(--neutral-soft)] border border-border flex items-center justify-center text-text-subtle hover:text-[#7c6dff] hover:border-[#7c6dff]/30 transition-colors shrink-0 shadow-sm">
                              <Mail size={14} />
                            </a>
                          </div>
                          <div>
                            <div className="font-semibold text-sm text-text-main truncate">
                              {c.name || "Unknown Name"}
                            </div>
                            {c.title && (
                              <div className="text-[11px] text-text-subtle flex items-center gap-1.5 mt-1.5 truncate">
                                <Briefcase size={12} className="shrink-0 opacity-70" />
                                <span className="truncate">{c.title}</span>
                              </div>
                            )}
                          </div>
                          <div className="text-[11px] text-text-muted font-mono mt-auto pt-3 border-t border-border/50 flex flex-col gap-1">
                            <div className="flex items-center gap-1.5 w-full">
                              <Mail size={11} className="shrink-0 opacity-70" />
                              <span className="truncate">{c.email}</span>
                            </div>
                            {c.number && (
                              <div className="flex items-center gap-1.5 w-full">
                                <Phone size={11} className="shrink-0 opacity-70" />
                                <span className="truncate">{c.number}</span>
                              </div>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </main>
    </div>
  );
}
