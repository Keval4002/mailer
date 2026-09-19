"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Send, Zap } from "lucide-react";

export default function TopNav() {
  const pathname = usePathname();

  const navLinks = [
    { href: "/", label: "Queue" },
    { href: "/applications", label: "Applications" },
    { href: "/campaign-builder", label: "Emailer" },
    { href: "/sequences", label: "Sequences" },
    { href: "/contacts", label: "Directory" },
    { href: "/reminders", label: "Notes" },
  ];

  return (
    <header className="py-6 pb-4 flex items-center justify-between flex-wrap gap-3 border-b border-border mb-8">
      {/* Brand */}
      <div className="flex items-center gap-2.5">
        <div className="w-7 h-7 rounded-lg flex items-center justify-center"
          style={{ background: "linear-gradient(135deg, #7c6dff 0%, #5b4fd4 100%)", boxShadow: "0 0 14px rgba(124,109,255,0.45)" }}>
          <Zap size={14} className="text-white" fill="white" />
        </div>
        <span className="text-base font-bold tracking-tight" style={{ background: "linear-gradient(120deg, var(--text-main) 0%, var(--text-subtle) 100%)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
          Mailer
        </span>
        <span className="hidden sm:inline text-xs font-medium text-text-subtle opacity-60 tracking-widest uppercase">/ personal mail scheduler</span>
      </div>

      {/* Nav pills */}
      <nav className="flex gap-0.5 bg-surface/70 backdrop-blur-xl border border-border rounded-full p-1 shadow-sm">
        {navLinks.map(({ href, label }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={`px-4 py-1.5 rounded-full text-sm font-semibold transition-all duration-200 ${
                active
                  ? "text-white shadow-md"
                  : "text-text-muted hover:text-text-main hover:bg-neutral-soft/60"
              }`}
              style={active ? {
                background: "linear-gradient(135deg, #7c6dff 0%, #5b4fd4 100%)",
                boxShadow: "0 0 10px rgba(124,109,255,0.4)",
              } : {}}
            >
              {label}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
