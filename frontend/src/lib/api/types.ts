export interface PaginatedResponse<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}

export interface InvoiceItemOut {
  id: string;
  line_number: number;
  description?: string;
  quantity?: string;
  unit_price?: string;
  tax_rate_percent?: string;
  discount?: string;
  line_total?: string;
  page_number?: number;
  source_text?: string;
}

export interface InvoiceOut {
  id: string;
  invoice_number: string;
  vendor_name: string;
  vendor_tax_id?: string;
  invoice_date?: string;
  po_number?: string;
  purchase_order_id?: string;
  currency?: string;
  subtotal?: string;
  tax_amount?: string;
  discount_amount?: string;
  grand_total?: string;
  status: string;
  created_at: string;
  items: InvoiceItemOut[];
}

export interface POItemOut {
  id: string;
  line_number: number;
  description?: string;
  quantity?: string;
  unit_price?: string;
  tax_rate_percent?: string;
  discount?: string;
  line_total?: string;
}

export interface POOut {
  id: string;
  po_number: string;
  vendor_name: string;
  vendor_tax_id?: string;
  po_date?: string;
  currency?: string;
  subtotal?: string;
  tax_amount?: string;
  discount_amount?: string;
  grand_total?: string;
  status: string;
  created_at: string;
  items: POItemOut[];
}

export interface CanonicalLineItemOut {
  line_number: number;
  description?: string;
  quantity?: string;
  unit_price?: string;
  line_total?: string;
  tax_rate_percent?: string;
  discount?: string;
  page_number?: number;
  source_text?: string;
}

export interface CanonicalExtractionOut {
  vendor_name?: string;
  invoice_number?: string;
  invoice_date?: string;
  due_date?: string;
  currency?: string;
  po_number?: string;
  subtotal?: string;
  tax_amount?: string;
  discount_amount?: string;
  grand_total?: string;
  line_items: CanonicalLineItemOut[];
}

export interface ExtractionDetailOut {
  extraction_id: string;
  document_id?: string;
  extraction_status: string;
  confidence?: number;
  provider_name: string;
  model_name: string;
  raw_extraction: Record<string, unknown>;
  normalized_extraction: Record<string, unknown>;
  created_at: string;
}

export interface EvidenceOut {
  id: string;
  anomaly_id: string;
  source_type: string;
  document_id?: string;
  field_path?: string;
  expected_value?: string;
  actual_value?: string;
  page_number?: number;
  source_text?: string;
  created_at: string;
}

export interface AnomalyOut {
  id: string;
  reconciliation_result_id: string;
  invoice_item_id?: string;
  po_item_id?: string;
  type: string;
  description: string;
  expected_value?: string;
  actual_value?: string;
  financial_impact?: string;
  status: string;
  created_at: string;
}

export interface ReconciliationResultOut {
  id: string;
  audit_id: string;
  overall_status: string;
  subtotal_diff?: string;
  tax_diff?: string;
  total_diff?: string;
  created_at: string;
  anomalies: AnomalyOut[];
}

export interface ReconcileResponse {
  audit_id: string;
  invoice_id: string;
  overall_status: string;
  result: ReconciliationResultOut;
}

export interface CorrectionRequest {
  audit_id: string;
  field_path: string;
  corrected_value?: string;
  original_value?: string;
  reason?: string;
  corrected_by?: string;
}

export interface CorrectionOut {
  id: string;
  audit_id: string;
  invoice_id: string;
  field_path: string;
  original_value?: string;
  corrected_value?: string;
  reason?: string;
  corrected_by?: string;
  created_at: string;
}

export interface ReconcileFromExtractionResponse {
  invoice_id: string;
  population: Record<string, unknown>;
  audit_id?: string;
  overall_status?: string;
  anomaly_count?: number;
}

export interface AuditOut {
  id: string;
  invoice_id: string;
  action: string;
  actor: string;
  previous_state: Record<string, unknown>;
  new_state: Record<string, unknown>;
  created_at: string;
  reconciliation_result?: ReconciliationResultOut;
}
