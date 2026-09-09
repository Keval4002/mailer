"use client";

import { useEffect, useState } from "react";
import api from "@/lib/api";
import { Users } from "lucide-react";
import TopNav from "@/app/components/TopNav";

export default function ContactsPage() {
  const [contacts, setContacts] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get("/api/contacts")
      .then((res) => {
        setContacts(res.data.contacts || []);
        setLoading(false);
      })
      .catch((err) => {
        console.error(err);
        setLoading(false);
      });
  }, []);

  return (
    <div className="w-[min(1100px,calc(100vw-32px))] mx-auto pb-20">
      <TopNav />
      <main>
        <div className="bg-surface border border-border rounded-xl shadow-sm overflow-hidden">
          <div className="flex items-center gap-3 p-6 border-b border-border">
            <Users size={24} className="text-accent" />
            <h2 className="m-0 text-lg font-semibold tracking-tight">Contact Directory</h2>
          </div>

          {loading ? (
            <div className="p-12 text-center text-text-muted">Loading contacts...</div>
          ) : contacts.length === 0 ? (
            <div className="p-12 text-center text-text-muted">
              No contacts found. Send a campaign to add contacts!
            </div>
          ) : (
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-surface-sunk/50 text-[11px] uppercase tracking-wider text-text-subtle font-semibold border-b border-border">
                  <th className="px-6 py-4">Name</th>
                  <th className="px-6 py-4">Email</th>
                  <th className="px-6 py-4">Company</th>
                  <th className="px-6 py-4 text-right">Sent Mails</th>
                </tr>
              </thead>
              <tbody>
                {contacts.map((c) => (
                  <tr
                    key={c.recipient_email}
                    className="border-b border-border last:border-0 hover:bg-neutral-soft transition-colors"
                  >
                    <td className="px-6 py-4 font-medium">{c.recipient_name || "—"}</td>
                    <td className="px-6 py-4 text-text-muted">{c.recipient_email}</td>
                    <td className="px-6 py-4 text-text-muted">{c.company || "—"}</td>
                    <td className="px-6 py-4 text-right font-mono">{c.sent_mails_count || 0}</td>
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
