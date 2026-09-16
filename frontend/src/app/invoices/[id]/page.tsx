import { apiClient } from '@/lib/api/client';
import ReconciliationWorkspace from '@/components/ReconciliationWorkspace';

export const dynamic = 'force-dynamic';

export default async function InvoiceReviewPage({ params }: { params: { id: string } }) {
  const { id } = params;
  
  // Fetch everything concurrently, but fail gracefully if extraction/recon doesn't exist
  const [invoice, po, extraction, reconciliation] = await Promise.all([
    apiClient.invoices.get(id),
    // Fetch PO only if we have an invoice with a PO ID
    apiClient.invoices.get(id).then(inv => 
      inv.purchase_order_id ? apiClient.purchaseOrders.get(inv.purchase_order_id).catch(() => null) : null
    ).catch(() => null),
    apiClient.extraction.get(id).catch(() => null),
    apiClient.reconciliation.get(id).catch(() => null),
  ]);

  return (
    <ReconciliationWorkspace 
      invoice={invoice} 
      po={po || undefined} 
      extraction={extraction} 
      reconciliation={reconciliation} 
    />
  );
}
