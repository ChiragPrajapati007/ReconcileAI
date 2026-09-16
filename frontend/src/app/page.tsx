import Link from 'next/link';
import { apiClient } from '@/lib/api/client';

export const dynamic = 'force-dynamic';

export default async function DashboardPage() {
  const invoicesResponse = await apiClient.invoices.list(1);
  const invoices = invoicesResponse.items || [];

  return (
    <div className="max-w-6xl mx-auto py-8 px-4">
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Reconciliation Dashboard</h1>
      
      <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="bg-gray-50 text-gray-500 text-xs uppercase tracking-wider border-b border-gray-200">
              <th className="py-3 px-4 font-semibold">Invoice #</th>
              <th className="py-3 px-4 font-semibold">Vendor</th>
              <th className="py-3 px-4 font-semibold">Status</th>
              <th className="py-3 px-4 font-semibold text-right">Created At</th>
              <th className="py-3 px-4 font-semibold text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {invoices.length === 0 ? (
              <tr>
                <td colSpan={5} className="py-8 text-center text-gray-500">
                  No invoices found.
                </td>
              </tr>
            ) : (
              invoices.map((invoice) => (
                <tr key={invoice.id} className="border-b border-gray-100 hover:bg-gray-50 transition-colors">
                  <td className="py-3 px-4 text-sm font-medium text-gray-900">{invoice.invoice_number || 'Unknown'}</td>
                  <td className="py-3 px-4 text-sm text-gray-600">{invoice.vendor_name || 'Unknown'}</td>
                  <td className="py-3 px-4">
                    <span className={`text-xs px-2 py-1 rounded font-bold uppercase ${
                      invoice.status === 'reconciled' ? 'bg-emerald-100 text-emerald-800' :
                      invoice.status === 'pending' ? 'bg-amber-100 text-amber-800' :
                      'bg-gray-100 text-gray-800'
                    }`}>
                      {invoice.status}
                    </span>
                  </td>
                  <td className="py-3 px-4 text-sm text-gray-500 text-right">
                    {new Date(invoice.created_at).toLocaleDateString()}
                  </td>
                  <td className="py-3 px-4 text-right">
                    <Link href={`/invoices/${invoice.id}`} className="text-sm font-semibold text-blue-600 hover:text-blue-800">
                      Review &rarr;
                    </Link>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
