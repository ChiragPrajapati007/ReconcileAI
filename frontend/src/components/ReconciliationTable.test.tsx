import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { describe, it, expect, vi } from 'vitest';
import ReconciliationTable from './ReconciliationTable';
import { InvoiceOut, POOut, ReconciliationResultOut } from '@/lib/api/types';

describe('ReconciliationTable', () => {
  const mockInvoice: InvoiceOut = {
    id: 'inv-123',
    invoice_number: 'INV-100',
    vendor_name: 'Test Vendor',
    status: 'reconciled',
    subtotal: '100.00',
    tax_amount: '10.00',
    grand_total: '110.00',
    created_at: new Date().toISOString(),
    items: [
      {
        id: 'item-1',
        line_number: 1,
        description: 'Test Item',
        quantity: '1.00',
        unit_price: '100.00',
        line_total: '100.00',
      }
    ]
  };

  const mockPO: POOut = {
    id: 'po-123',
    po_number: 'PO-100',
    vendor_name: 'Test Vendor',
    status: 'open',
    subtotal: '100.00',
    tax_amount: '5.00',
    grand_total: '105.00',
    created_at: new Date().toISOString(),
    items: [
      {
        id: 'po-item-1',
        line_number: 1,
        description: 'Test Item',
        quantity: '1.00',
        unit_price: '100.00',
        line_total: '100.00',
      }
    ]
  };

  const mockReconciliation: ReconciliationResultOut = {
    id: 'rec-123',
    audit_id: 'audit-123',
    overall_status: 'anomaly',
    subtotal_diff: '0.00',
    tax_diff: '5.00',
    total_diff: '5.00',
    created_at: new Date().toISOString(),
    anomalies: [
      {
        id: 'anom-1',
        reconciliation_result_id: 'rec-123',
        type: 'TOTAL_MISMATCH',
        description: 'Tax mismatch',
        expected_value: '5.00',
        actual_value: '10.00',
        status: 'open',
        created_at: new Date().toISOString()
      }
    ]
  };

  it('renders line item data correctly', () => {
    render(<ReconciliationTable invoice={mockInvoice} po={mockPO} />);
    
    // Check that the line item description is rendered
    expect(screen.getByText('Test Item')).toBeTruthy();
    
    // Check line totals (expected vs actual)
    // There are multiple "$100.00" on the screen (PO and Inv subtotal, line totals)
    // We can at least check if elements with $100.00 exist
    const hundreds = screen.getAllByText('$100.00');
    expect(hundreds.length).toBeGreaterThan(0);
  });

  it('displays expected vs actual values for totals', () => {
    render(<ReconciliationTable invoice={mockInvoice} po={mockPO} reconciliation={mockReconciliation} />);
    
    // Tax row should have Expected $5.00, Actual $10.00, Difference +$5.00
    expect(screen.getByText('$5.00')).toBeTruthy(); // PO Tax
    const tens = screen.getAllByText('$10.00');
    expect(tens.length).toBeGreaterThan(0); // Invoice Tax
    const diffs = screen.getAllByText('+$5.00');
    expect(diffs.length).toBeGreaterThan(0); // Difference
  });

  it('displays anomaly state visually', () => {
    render(<ReconciliationTable invoice={mockInvoice} po={mockPO} reconciliation={mockReconciliation} />);
    
    // The anomaly badge should be visible (type: TOTAL_MISMATCH -> replaced with TOTAL MISMATCH)
    expect(screen.getByText('TOTAL MISMATCH')).toBeTruthy();
    
    // The overall status should be 'anomaly'
    expect(screen.getByText('anomaly')).toBeTruthy();
  });

  it('renders correctly without anomalies', () => {
    const perfectReconciliation: ReconciliationResultOut = {
      ...mockReconciliation,
      overall_status: 'matched',
      anomalies: []
    };
    
    render(<ReconciliationTable invoice={mockInvoice} po={mockPO} reconciliation={perfectReconciliation} />);
    
    expect(screen.getByText('matched')).toBeTruthy();
    // ✓ should be visible for items with no anomalies
    const checks = screen.getAllByText('✓');
    expect(checks.length).toBeGreaterThan(0);
  });
});
