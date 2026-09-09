import React from 'react';
import { Mail, Clock, AlertCircle } from 'lucide-react';
import GmailConnect from '../components/GmailConnect';

export default function Dashboard() {
  return (
    <div className="space-y-6">
      <GmailConnect />
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-surface border border-border rounded-xl p-6 shadow-sm">
          <div className="flex items-center gap-3 text-text-muted mb-2">
            <Mail size={18} />
            <span className="text-sm font-semibold uppercase tracking-wider">Pending</span>
          </div>
          <div className="text-3xl font-bold">0</div>
        </div>
        <div className="bg-surface border border-border rounded-xl p-6 shadow-sm">
          <div className="flex items-center gap-3 text-text-muted mb-2">
            <Clock size={18} />
            <span className="text-sm font-semibold uppercase tracking-wider">Sent Today</span>
          </div>
          <div className="text-3xl font-bold">0</div>
        </div>
        <div className="bg-surface border border-border rounded-xl p-6 shadow-sm">
          <div className="flex items-center gap-3 text-danger mb-2">
            <AlertCircle size={18} />
            <span className="text-sm font-semibold uppercase tracking-wider text-danger">Failed</span>
          </div>
          <div className="text-3xl font-bold text-danger">0</div>
        </div>
      </div>

      <div className="bg-surface border border-border rounded-xl shadow-sm overflow-hidden">
        <div className="p-6 border-b border-border">
          <h2 className="m-0 text-lg font-semibold tracking-tight">Active Workflows</h2>
        </div>
        <div className="p-12 text-center text-text-muted">
          No active workflows. Head over to the Campaign Builder to schedule some emails.
        </div>
      </div>
    </div>
  );
}
