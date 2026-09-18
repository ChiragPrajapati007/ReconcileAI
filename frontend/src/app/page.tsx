import { apiClient } from '@/lib/api/client';
import DocumentUpload from './DocumentUpload';
import Link from 'next/link';
import { InvoiceOut } from '@/lib/api/types';

export const dynamic = 'force-dynamic';

export default async function DashboardPage() {
  const invoicesResponse = await apiClient.invoices.list(1);
  const invoices = invoicesResponse.items || [];

  return (
    <div className="max-w-6xl mx-auto py-8 px-4 flex flex-col md:flex-row gap-8">
      
      {/* Left Column: Invoice List */}
      <div className="flex-1">
        <div className="flex justify-between items-center mb-6">
          <h1 className="text-2xl font-bold text-gray-900">Reconciliation Dashboard</h1>
        </div>
        
        <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-gray-50 text-gray-500 text-xs uppercase tracking-wider border-b border-gray-200">
                <th className="py-3 px-4 font-semibold">Invoice #</th>
                <th className="py-3 px-4 font-semibold">Vendor</th>
                <th className="py-3 px-4 font-semibold">Status</th>
                <th className="py-3 px-4 font-semibold text-right">Total</th>
                <th className="py-3 px-4 font-semibold text-right">Date</th>
                <th className="py-3 px-4 font-semibold"></th>
              </tr>
            </thead>
            <tbody>
              {invoices.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-gray-500">
                    No invoices found. Upload a document to begin.
                  </td>
                </tr>
              ) : (
                invoices.map((inv: InvoiceOut) => (
                  <tr key={inv.id} className="border-b border-gray-100 hover:bg-gray-50 transition-colors">
                    <td className="py-3 px-4 text-sm font-medium text-gray-900">{inv.invoice_number}</td>
                    <td className="py-3 px-4 text-sm text-gray-600">{inv.vendor_name}</td>
                    <td className="py-3 px-4 text-sm">
                      <div className="flex gap-4 items-center">
                        <Link 
                          href={`/workspace/${inv.id}`}
                          className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                            inv.status === 'RECONCILED' ? 'bg-green-100 text-green-800' :
                            inv.status === 'UNDER_AUDIT' ? 'bg-yellow-100 text-yellow-800' :
                            'bg-gray-100 text-gray-800'
                          }`}
                        >
                          {inv.status}
                        </Link>
                      </div>
                    </td>
                    <td className="py-3 px-4 text-sm font-medium text-gray-900 text-right">
                      {inv.currency} {inv.grand_total}
                    </td>
                    <td className="py-3 px-4 text-sm text-gray-500 text-right">
                      {inv.invoice_date ? new Date(inv.invoice_date).toLocaleDateString() : '—'}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Right Column: Ingestion/Upload */}
      <div className="w-full md:w-[350px]">
        <div className="bg-white border border-gray-200 rounded-lg shadow-sm p-6 sticky top-6">
          <h2 className="text-lg font-bold text-gray-900 mb-4">Ingest Document</h2>
          <p className="text-gray-600 mb-8 max-w-2xl mx-auto">
            Upload invoices or purchase orders to automatically extract data, and attempt reconciliation if a matching PO exists.
          </p>
          <DocumentUpload />
        </div>
      </div>

    </div>
  );
}
