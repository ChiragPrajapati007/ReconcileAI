import { apiClient } from '@/lib/api/client';
import POCreateForm from './POCreateForm';
import { POOut } from '@/lib/api/types';

export const dynamic = 'force-dynamic';

export default async function PurchaseOrdersPage() {
  let pos: POOut[] = [];
  try {
    const posResponse = await apiClient.purchaseOrders.list(1);
    pos = posResponse.items || [];
  } catch (e) {
    console.error("Failed to fetch POs", e);
  }

  return (
    <div className="max-w-6xl mx-auto py-8 px-4 flex flex-col md:flex-row gap-8">
      
      {/* Left Column: PO List */}
      <div className="flex-1">
        <div className="flex justify-between items-center mb-6">
          <h1 className="text-2xl font-bold text-gray-900">Purchase Orders</h1>
        </div>
        
        <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-gray-50 text-gray-500 text-xs uppercase tracking-wider border-b border-gray-200">
                <th className="py-3 px-4 font-semibold">PO #</th>
                <th className="py-3 px-4 font-semibold">Vendor</th>
                <th className="py-3 px-4 font-semibold text-right">Total</th>
                <th className="py-3 px-4 font-semibold text-right">Date</th>
              </tr>
            </thead>
            <tbody>
              {pos.length === 0 ? (
                <tr>
                  <td colSpan={4} className="py-8 text-center text-gray-500">
                    No purchase orders found.
                  </td>
                </tr>
              ) : (
                pos.map((po: POOut) => (
                  <tr key={po.id} className="border-b border-gray-100 hover:bg-gray-50 transition-colors">
                    <td className="py-3 px-4 text-sm font-medium text-gray-900">{po.po_number}</td>
                    <td className="py-3 px-4 text-sm text-gray-600">{po.vendor_name}</td>
                    <td className="py-3 px-4 text-sm font-medium text-gray-900 text-right">
                      {po.currency} {po.grand_total}
                    </td>
                    <td className="py-3 px-4 text-sm text-gray-500 text-right">
                      {po.po_date ? new Date(po.po_date).toLocaleDateString() : '—'}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Right Column: Create PO Form */}
      <div className="w-full md:w-[400px]">
        <div className="bg-white border border-gray-200 rounded-lg shadow-sm p-6 sticky top-6">
          <h2 className="text-lg font-bold text-gray-900 mb-4">Create Purchase Order</h2>
          <p className="text-sm text-gray-500 mb-6">
            Add a Purchase Order to the system before uploading an invoice to demonstrate reconciliation matching.
          </p>
          <POCreateForm />
        </div>
      </div>

    </div>
  );
}
