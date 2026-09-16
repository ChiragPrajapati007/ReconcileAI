import React from 'react';
import { ReconcileResponse } from '@/lib/api/types';

export default function AuditTimeline({ reconciliation }: { reconciliation?: ReconcileResponse | null }) {
  if (!reconciliation) return null;

  return (
    <div className="bg-white border-b border-gray-200 px-6 py-4 shrink-0">
      <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Audit History</h3>
      <div className="flex items-center text-sm">
        <div className="w-2 h-2 rounded-full bg-blue-500 mr-2"></div>
        <span className="font-medium text-gray-900 mr-2">Latest Run:</span>
        <span className="text-gray-600 font-mono text-xs">{reconciliation.audit_id}</span>
        <span className="mx-2 text-gray-300">|</span>
        <span className="text-gray-500">{new Date(reconciliation.result.created_at).toLocaleString()}</span>
      </div>
    </div>
  );
}
