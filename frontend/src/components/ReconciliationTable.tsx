import React from 'react';
import { InvoiceOut, POOut, ReconciliationResultOut, AnomalyOut } from '@/lib/api/types';

interface ReconciliationTableProps {
  po?: POOut;
  invoice: InvoiceOut;
  reconciliation?: ReconciliationResultOut;
  onAnomalyClick?: (anomaly: AnomalyOut) => void;
}

const formatCurrency = (val?: string) => {
  if (!val) return '-';
  const num = parseFloat(val);
  return isNaN(num) ? val : `$${num.toFixed(2)}`;
};

export default function ReconciliationTable({ po, invoice, reconciliation, onAnomalyClick }: ReconciliationTableProps) {
  // Map anomalies to fields
  const anomaliesByField: Record<string, AnomalyOut[]> = {};
  
  if (reconciliation?.anomalies) {
    for (const a of reconciliation.anomalies) {
      if (a.po_item_id) {
        if (!anomaliesByField[`po_item_${a.po_item_id}`]) anomaliesByField[`po_item_${a.po_item_id}`] = [];
        anomaliesByField[`po_item_${a.po_item_id}`].push(a);
      } else if (a.invoice_item_id) {
        if (!anomaliesByField[`inv_item_${a.invoice_item_id}`]) anomaliesByField[`inv_item_${a.invoice_item_id}`] = [];
        anomaliesByField[`inv_item_${a.invoice_item_id}`].push(a);
      } else {
        if (!anomaliesByField[a.type]) anomaliesByField[a.type] = [];
        anomaliesByField[a.type].push(a);
      }
    }
  }

  const renderStatus = (anomalies?: AnomalyOut[]) => {
    if (!anomalies || anomalies.length === 0) {
      return <span className="text-emerald-600 font-bold">✓</span>;
    }
    
    // Check if any is open
    const hasOpen = anomalies.some(a => a.status === 'open');
    const hasReviewed = anomalies.some(a => a.status === 'reviewed');
    
    return (
      <div className="flex flex-col gap-1 items-end">
        {anomalies.map(a => (
          <button 
            key={a.id} 
            onClick={() => onAnomalyClick?.(a)}
            className={`text-xs px-2 py-0.5 border rounded-sm font-semibold transition-colors
              ${a.status === 'open' ? 'bg-red-50 text-red-700 border-red-200 hover:bg-red-100' : ''}
              ${a.status === 'reviewed' ? 'bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100' : ''}
              ${a.status === 'resolved' ? 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100' : ''}
            `}
            title={a.description}
          >
            {a.type.replace('_', ' ')}
          </button>
        ))}
      </div>
    );
  };

  const getDiff = (expected?: string, actual?: string) => {
    if (!expected || !actual) return null;
    const diff = parseFloat(actual) - parseFloat(expected);
    if (Math.abs(diff) < 0.01) return null;
    return diff;
  };

  const renderRow = (label: React.ReactNode, expected?: string, actual?: string, anomalies?: AnomalyOut[], forceDiffVal?: string) => {
    const diff = forceDiffVal !== undefined ? parseFloat(forceDiffVal) : getDiff(expected, actual);
    const hasDiff = diff !== null && Math.abs(diff) >= 0.01;
    const isAnomalous = anomalies && anomalies.length > 0;

    return (
      <tr className={`border-b border-gray-100 ${isAnomalous ? 'bg-red-50/20' : ''}`}>
        <td className="py-3 px-4 text-sm font-medium text-gray-800">{label}</td>
        <td className="py-3 px-4 text-sm font-mono text-gray-600 text-right">{formatCurrency(expected)}</td>
        <td className="py-3 px-4 text-sm font-mono text-gray-900 text-right font-semibold">{formatCurrency(actual)}</td>
        <td className={`py-3 px-4 text-sm font-mono text-right ${hasDiff ? (diff > 0 ? 'text-red-600' : 'text-orange-600') : 'text-gray-400'}`}>
          {hasDiff ? (diff > 0 ? `+${formatCurrency(diff.toString())}` : formatCurrency(diff.toString())) : '-'}
        </td>
        <td className="py-3 px-4 text-right">
          {renderStatus(anomalies)}
        </td>
      </tr>
    );
  };

  // Match items based on line_number for visual alignment
  const matchedItems = [];
  const poItems = po?.items || [];
  const invItems = invoice.items || [];
  
  const maxLines = Math.max(
    ...poItems.map(i => i.line_number), 
    ...invItems.map(i => i.line_number),
    0
  );

  for (let i = 1; i <= maxLines; i++) {
    const pItem = poItems.find(p => p.line_number === i);
    const iItem = invItems.find(inv => inv.line_number === i);
    
    if (pItem || iItem) {
      let anomalies: AnomalyOut[] = [];
      if (pItem && anomaliesByField[`po_item_${pItem.id}`]) {
        anomalies = anomalies.concat(anomaliesByField[`po_item_${pItem.id}`]);
      }
      if (iItem && anomaliesByField[`inv_item_${iItem.id}`]) {
        anomalies = anomalies.concat(anomaliesByField[`inv_item_${iItem.id}`]);
      }

      matchedItems.push({
        line: i,
        desc: iItem?.description || pItem?.description || `Line ${i}`,
        expected: pItem?.line_total,
        actual: iItem?.line_total,
        anomalies,
      });
    }
  }

  return (
    <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
      <div className="px-4 py-3 bg-gray-50 border-b border-gray-200 flex justify-between items-center">
        <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wider">Reconciliation Audit</h3>
        {reconciliation && (
          <span className={`text-xs px-2 py-1 rounded font-bold uppercase ${
            reconciliation.overall_status === 'matched' ? 'bg-emerald-100 text-emerald-800' :
            reconciliation.overall_status === 'anomaly' ? 'bg-red-100 text-red-800' :
            'bg-gray-100 text-gray-800'
          }`}>
            {reconciliation.overall_status}
          </span>
        )}
      </div>
      
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="bg-gray-50 text-gray-500 text-xs uppercase tracking-wider border-b border-gray-200">
              <th className="py-3 px-4 font-semibold">Description</th>
              <th className="py-3 px-4 font-semibold text-right">Expected (PO)</th>
              <th className="py-3 px-4 font-semibold text-right">Actual (Invoice)</th>
              <th className="py-3 px-4 font-semibold text-right">Difference</th>
              <th className="py-3 px-4 font-semibold text-right">Status</th>
            </tr>
          </thead>
          <tbody>
            {matchedItems.map(item => (
              <React.Fragment key={`line-${item.line}`}>
                {renderRow(
                  <div className="flex flex-col">
                    <span>{item.desc}</span>
                    <span className="text-xs text-gray-400">Line {item.line}</span>
                  </div>,
                  item.expected,
                  item.actual,
                  item.anomalies
                )}
              </React.Fragment>
            ))}
            
            <tr className="bg-gray-50">
              <td colSpan={5} className="py-2 px-4 text-xs font-semibold text-gray-500 uppercase tracking-wider">Totals</td>
            </tr>
            
            {renderRow('Subtotal', po?.subtotal, invoice.subtotal, anomaliesByField['TOTAL_MISMATCH']?.filter(a => a.description.includes('Subtotal')), reconciliation?.subtotal_diff)}
            {renderRow('Tax', po?.tax_amount, invoice.tax_amount, anomaliesByField['TOTAL_MISMATCH']?.filter(a => a.description.includes('Tax')), reconciliation?.tax_diff)}
            {renderRow('Grand Total', po?.grand_total, invoice.grand_total, anomaliesByField['TOTAL_MISMATCH']?.filter(a => a.description.includes('Grand Total')), reconciliation?.total_diff)}
          </tbody>
        </table>
      </div>
    </div>
  );
}
