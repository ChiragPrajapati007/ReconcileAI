const BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    public message: string
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });

  if (!response.ok) {
    let errCode = 'UNKNOWN_ERROR';
    let errMessage = response.statusText;
    try {
      const errorData = await response.json();
      if (errorData.error && errorData.error.code) {
        errCode = errorData.error.code;
        errMessage = errorData.error.message;
      } else if (errorData.detail && errorData.detail.code) {
        errCode = errorData.detail.code;
        errMessage = errorData.detail.message;
      } else if (errorData.detail && typeof errorData.detail === 'string') {
        errMessage = errorData.detail;
      }
    } catch {
      // Ignored if not JSON
    }
    throw new ApiError(response.status, errCode, errMessage);
  }

  return response.json();
}

async function fetchMultipartApi<T>(path: string, formData: FormData): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const response = await fetch(url, {
    method: 'POST',
    body: formData,
    // Note: Do not set Content-Type header manually when using FormData
  });

  if (!response.ok) {
    let errCode = 'UNKNOWN_ERROR';
    let errMessage = response.statusText;
    try {
      const errorData = await response.json();
      if (errorData.error && errorData.error.code) {
        errCode = errorData.error.code;
        errMessage = errorData.error.message;
      } else if (errorData.detail && errorData.detail.code) {
        errCode = errorData.detail.code;
        errMessage = errorData.detail.message;
      } else if (errorData.detail && typeof errorData.detail === 'string') {
        errMessage = errorData.detail;
      }
    } catch {
      // Ignored
    }
    throw new ApiError(response.status, errCode, errMessage);
  }

  return response.json();
}

import {
  PaginatedResponse,
  InvoiceOut,
  POOut,
  ExtractionDetailOut,
  ReconcileResponse,
  EvidenceOut,
  AnomalyOut,
  CorrectionRequest,
  CorrectionOut,
  ReconcileFromExtractionResponse,
  AuditOut,
} from './types';

export const apiClient = {
  invoices: {
    list: (page = 1) => 
      fetchApi<PaginatedResponse<InvoiceOut>>(`/api/invoices?page=${page}`),
    get: (id: string) => 
      fetchApi<InvoiceOut>(`/api/invoices/${id}`),
    getAudit: (id: string) =>
      fetchApi<AuditOut>(`/api/invoices/${id}/audit`),
  },
  purchaseOrders: {
    list: (page = 1) =>
      fetchApi<PaginatedResponse<POOut>>(`/api/purchase-orders?page=${page}`),
    create: (payload: Record<string, unknown>) =>
      fetchApi<POOut>(`/api/purchase-orders`, {
        method: 'POST',
        body: JSON.stringify(payload)
      }),
    get: (id: string) => 
      fetchApi<POOut>(`/api/purchase-orders/${id}`),
  },
  extraction: {
    ingest: (file: File) => {
      const formData = new FormData();
      formData.append('file', file);
      return fetchMultipartApi<Record<string, unknown>>(`/api/extraction/ingest`, formData);
    },
    get: (invoiceId: string) => 
      fetchApi<ExtractionDetailOut>(`/api/extraction/${invoiceId}`),
    submitCorrection: (invoiceId: string, payload: CorrectionRequest) =>
      fetchApi<CorrectionOut>(`/api/extraction/${invoiceId}/corrections`, {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    reconcile: (invoiceId: string, allowReview = true) =>
      fetchApi<ReconcileFromExtractionResponse>(`/api/extraction/${invoiceId}/reconcile`, {
        method: 'POST',
        body: JSON.stringify({ allow_review: allowReview }),
      }),
  },
  reconciliation: {
    get: (invoiceId: string) => 
      fetchApi<ReconcileResponse>(`/api/reconciliation/${invoiceId}`),
    updateAnomalyStatus: (anomalyId: string, status: string) =>
      fetchApi<AnomalyOut>(`/api/reconciliation/anomalies/${anomalyId}`, {
        method: 'PATCH',
        body: JSON.stringify({ status }),
      }),
    getEvidence: (anomalyId: string) =>
      fetchApi<EvidenceOut[]>(`/api/reconciliation/anomalies/${anomalyId}/evidence`),
  }
};
