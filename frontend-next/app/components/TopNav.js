"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Send } from "lucide-react";

export default function TopNav() {
  const pathname = usePathname();

  const navLinks = [
    { href: "/", label: "Queue" },
    { href: "/contacts", label: "Contacts" },
    { href: "/reminders", label: "Notes" },
  ];

  return (
    <header className="py-6 pb-4 flex items-center justify-between flex-wrap gap-3 border-b border-border mb-8">
      <div className="flex items-center gap-3">
        <h1 className="m-0 text-xl font-bold tracking-tight bg-gradient-to-br from-accent to-text-subtle bg-clip-text text-transparent">
          Personal Mail Scheduler
        </h1>
        <div className="w-2.5 h-2.5 rounded-full bg-accent shadow-[0_0_12px_var(--accent)] animate-pulse" />
      </div>

      <nav className="flex gap-1 bg-surface/80 backdrop-blur-xl border border-border rounded-full p-1.5 shadow-sm">
        {navLinks.map(({ href, label }) => (
          <Link
            key={href}
            href={href}
            className={`px-4 py-2 rounded-full text-sm font-medium transition-all ${
              pathname === href
                ? "bg-neutral-soft text-text-main"
                : "text-text-muted hover:bg-neutral-soft/50 hover:text-text-main"
            }`}
          >
            {label}
          </Link>
        ))}
      </nav>

      <div className="flex items-center gap-2">
        <Link
          href="/campaign-builder"
          className="inline-flex items-center justify-center gap-2 min-h-[36px] px-4 py-2 rounded-md border border-transparent bg-accent text-[var(--surface)] text-sm font-medium shadow-sm transition-all hover:-translate-y-[1px] hover:shadow-md hover:bg-accent/90 focus:outline-none focus:ring-4 focus:ring-accent-soft"
        >
          <Send size={16} />
          Campaign Builder
        </Link>
      </div>
    </header>
  );
}
