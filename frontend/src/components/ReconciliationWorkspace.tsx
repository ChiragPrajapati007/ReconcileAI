'use client';

import React, { useState } from 'react';
import { InvoiceOut, POOut, ExtractionDetailOut, ReconcileResponse, AnomalyOut, EvidenceOut } from '@/lib/api/types';
import { apiClient } from '@/lib/api/client';
import ReconciliationTable from '@/components/ReconciliationTable';
import AuditTimeline from '@/components/AuditTimeline';

interface WorkspaceProps {
  invoice: InvoiceOut;
  po?: POOut;
  extraction?: ExtractionDetailOut | null;
  reconciliation?: ReconcileResponse | null;
}

export default function ReconciliationWorkspace({ invoice, po, extraction, reconciliation: initialReconciliation }: WorkspaceProps) {
  const [reconciliation, setReconciliation] = useState(initialReconciliation);
  const [selectedAnomaly, setSelectedAnomaly] = useState<AnomalyOut | null>(null);
  const [evidence, setEvidence] = useState<EvidenceOut[]>([]);
  const [loadingEvidence, setLoadingEvidence] = useState(false);
  const [resolving, setResolving] = useState(false);
  
  // Correction form state
  const [correctionValue, setCorrectionValue] = useState('');
  const [correctionReason, setCorrectionReason] = useState('');
  const [submittingCorrection, setSubmittingCorrection] = useState(false);

  const handleAnomalyClick = async (anomaly: AnomalyOut) => {
    setSelectedAnomaly(anomaly);
    setLoadingEvidence(true);
    try {
      const ev = await apiClient.reconciliation.getEvidence(anomaly.id);
      setEvidence(ev);
    } catch (e) {
      console.error('Failed to load evidence', e);
    } finally {
      setLoadingEvidence(false);
    }
  };

  const markReviewed = async () => {
    if (!selectedAnomaly) return;
    try {
      const updated = await apiClient.reconciliation.updateAnomalyStatus(selectedAnomaly.id, 'reviewed');
      // Update local state
      if (reconciliation) {
        setReconciliation({
          ...reconciliation,
          result: {
            ...reconciliation.result,
            anomalies: reconciliation.result.anomalies.map(a => 
              a.id === updated.id ? updated : a
            )
          }
        });
        setSelectedAnomaly(updated);
      }
    } catch (e) {
      console.error('Failed to mark reviewed', e);
    }
  };

  const rerunReconciliation = async () => {
    setResolving(true);
    try {
      await apiClient.extraction.reconcile(invoice.id, true);
      // Re-fetch reconciliation
      const newRecon = await apiClient.reconciliation.get(invoice.id);
      setReconciliation(newRecon);
      setSelectedAnomaly(null);
    } catch (e) {
      console.error('Failed to rerun reconciliation', e);
    } finally {
      setResolving(false);
    }
  };

  const submitCorrection = async () => {
    if (!selectedAnomaly || !reconciliation || !reconciliation.result) return;
    setSubmittingCorrection(true);
    try {
      // Derive field path based on anomaly description/type for Phase 8 demonstration
      let fieldPath = 'grand_total'; // Fallback
      const desc = selectedAnomaly.description.toLowerCase();
      
      if (desc.includes('subtotal')) fieldPath = 'subtotal';
      else if (desc.includes('tax')) fieldPath = 'tax_amount';
      else if (desc.includes('grand total') || desc.includes('total mismatch')) fieldPath = 'grand_total';
      else if (selectedAnomaly.invoice_item_id) {
        // Find line number
        const item = invoice.items.find(i => i.id === selectedAnomaly.invoice_item_id);
        if (item) {
          fieldPath = `line_items[${item.line_number - 1}].line_total`;
        }
      }

      await apiClient.extraction.submitCorrection(invoice.id, {
        audit_id: reconciliation.result.audit_id,
        field_path: fieldPath,
        corrected_value: correctionValue,
        original_value: selectedAnomaly.actual_value,
        reason: correctionReason || 'Human reviewer correction',
        corrected_by: 'Reviewer'
      });
      
      // Clear form
      setCorrectionValue('');
      setCorrectionReason('');
      
      // Auto mark as reviewed
      await markReviewed();
      
    } catch (e) {
      console.error('Failed to submit correction', e);
    } finally {
      setSubmittingCorrection(false);
    }
  };

  return (
    <div className="flex flex-col h-screen bg-gray-50 overflow-hidden">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-6 py-4 flex justify-between items-center shrink-0">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Review Invoice {invoice.invoice_number}</h1>
          <p className="text-sm text-gray-500">Vendor: {invoice.vendor_name} {po ? `| PO: ${po.po_number}` : ''}</p>
        </div>
        <div className="flex gap-4 items-center">
          {extraction && (
            <div className="flex flex-col items-end">
              <span className="text-xs font-semibold text-gray-500 uppercase">Extraction Confidence</span>
              <span className={`text-sm font-bold ${
                extraction.extraction_status === 'success' ? 'text-emerald-600' :
                extraction.extraction_status === 'blocked' ? 'text-red-600' : 'text-gray-600'
              }`}>
                {(extraction.confidence !== undefined ? Math.round(extraction.confidence * 100) : 0)}%
              </span>
            </div>
          )}
          <button 
            onClick={rerunReconciliation}
            disabled={resolving}
            className="bg-gray-900 text-white px-4 py-2 rounded-md text-sm font-semibold hover:bg-gray-800 disabled:opacity-50"
          >
            {resolving ? 'Running...' : 'Rerun Reconciliation'}
          </button>
        </div>
      </header>

      <AuditTimeline reconciliation={reconciliation} />

      {/* Main Content Area */}
      <div className="flex flex-col md:flex-row flex-1 overflow-hidden">
        {/* Left/Main Column: Table */}
        <div className="flex-1 overflow-y-auto p-6">
          {!extraction ? (
            <div className="bg-white p-8 text-center rounded-lg border border-gray-200">
              <h2 className="text-lg font-semibold text-gray-700">No Extraction Data</h2>
              <p className="text-gray-500 mt-2">Upload a document to extract invoice data before reconciling.</p>
            </div>
          ) : !reconciliation ? (
            <div className="bg-white p-8 text-center rounded-lg border border-gray-200">
              <h2 className="text-lg font-semibold text-gray-700">Awaiting Reconciliation</h2>
              <p className="text-gray-500 mt-2">Click &quot;Rerun Reconciliation&quot; to generate the audit report.</p>
            </div>
          ) : (
            <ReconciliationTable 
              po={po} 
              invoice={invoice} 
              reconciliation={reconciliation.result} 
              onAnomalyClick={handleAnomalyClick} 
            />
          )}
        </div>

        {/* Right Column: Evidence & Action Panel */}
        {selectedAnomaly && (
          <div className="w-full md:w-96 h-[50vh] md:h-auto bg-white border-t md:border-t-0 md:border-l border-gray-200 flex flex-col shrink-0">
            <div className="p-4 border-b border-gray-200 bg-gray-50 flex justify-between items-center">
              <h3 className="font-semibold text-gray-800">Anomaly Details</h3>
              <button onClick={() => setSelectedAnomaly(null)} className="text-gray-400 hover:text-gray-600">✕</button>
            </div>
            
            <div className="p-6 flex-1 overflow-y-auto">
              <div className="mb-6">
                <span className={`inline-block text-xs px-2 py-1 rounded font-bold uppercase mb-2 ${
                  selectedAnomaly.status === 'open' ? 'bg-red-100 text-red-800' :
                  selectedAnomaly.status === 'reviewed' ? 'bg-amber-100 text-amber-800' :
                  'bg-emerald-100 text-emerald-800'
                }`}>
                  {selectedAnomaly.status}
                </span>
                <h4 className="text-lg font-semibold text-gray-900">{selectedAnomaly.type.replace('_', ' ')}</h4>
                <p className="text-sm text-gray-600 mt-1">{selectedAnomaly.description}</p>
              </div>

              <div className="bg-gray-50 border border-gray-200 rounded p-4 mb-6">
                <div className="flex justify-between mb-2">
                  <span className="text-xs font-semibold text-gray-500 uppercase">Expected</span>
                  <span className="font-mono text-sm">{selectedAnomaly.expected_value || '-'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-xs font-semibold text-gray-500 uppercase">Actual</span>
                  <span className="font-mono text-sm text-red-600">{selectedAnomaly.actual_value || '-'}</span>
                </div>
              </div>

              <div className="mb-6">
                <h4 className="text-sm font-semibold text-gray-800 mb-3 uppercase tracking-wider">Source Evidence</h4>
                {loadingEvidence ? (
                  <p className="text-sm text-gray-500">Loading evidence...</p>
                ) : evidence.length === 0 ? (
                  <p className="text-sm text-gray-500 italic">No evidence available.</p>
                ) : (
                  <div className="space-y-4">
                    {evidence.map(ev => (
                      <div key={ev.id} className="border border-indigo-100 bg-indigo-50/30 p-3 rounded">
                        <div className="flex justify-between items-center mb-2">
                          <span className="text-xs font-semibold text-indigo-800 uppercase">{ev.field_path}</span>
                          {ev.page_number && <span className="text-xs text-indigo-600 font-mono">Page {ev.page_number}</span>}
                        </div>
                        {ev.source_text && (
                          <div className="bg-white border border-indigo-100 p-2 text-sm text-gray-700 italic rounded">
                            &quot;{ev.source_text}&quot;
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Correction Form */}
              <div className="mt-8 pt-6 border-t border-gray-200">
                <h4 className="text-sm font-semibold text-gray-800 mb-3 uppercase tracking-wider">Submit Correction</h4>
                <div className="space-y-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-700 mb-1">Corrected Value</label>
                    <input 
                      type="text" 
                      value={correctionValue}
                      onChange={e => setCorrectionValue(e.target.value)}
                      placeholder={selectedAnomaly.expected_value || '0.00'}
                      className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-700 mb-1">Reason (Optional)</label>
                    <input 
                      type="text" 
                      value={correctionReason}
                      onChange={e => setCorrectionReason(e.target.value)}
                      placeholder="E.g., Vendor applied 10% discount"
                      className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
                    />
                  </div>
                  <button 
                    onClick={submitCorrection}
                    disabled={submittingCorrection || !correctionValue}
                    className="w-full bg-blue-600 text-white py-2 rounded font-semibold text-sm hover:bg-blue-700 transition-colors disabled:opacity-50"
                  >
                    {submittingCorrection ? 'Submitting...' : 'Apply Correction'}
                  </button>
                </div>
              </div>

              {/* Actions */}
              <div className="mt-6">
                {selectedAnomaly.status === 'open' && (
                  <button 
                    onClick={markReviewed}
                    className="w-full bg-amber-100 text-amber-800 border border-amber-200 py-2 rounded font-semibold text-sm hover:bg-amber-200 transition-colors"
                  >
                    Mark as Reviewed
                  </button>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
