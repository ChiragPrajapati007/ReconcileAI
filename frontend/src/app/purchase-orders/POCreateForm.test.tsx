/// <reference types="@testing-library/jest-dom" />
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';
import POCreateForm from './POCreateForm';
import { apiClient } from '@/lib/api/client';
import { useRouter } from 'next/navigation';

// Mock next/navigation
vi.mock('next/navigation', () => ({
  useRouter: vi.fn(),
}));

// Mock API client
vi.mock('@/lib/api/client', () => ({
  apiClient: {
    purchaseOrders: {
      create: vi.fn(),
    },
  },
}));

describe('POCreateForm Component', () => {
  const mockRefresh = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (useRouter as any).mockReturnValue({ refresh: mockRefresh });
  });

  it('renders required PO fields', () => {
    render(<POCreateForm />);
    
    expect(screen.getByPlaceholderText(/e.g. PO-1001/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/e.g. Acme Supplies/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Add Item/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Create PO/i })).toBeInTheDocument();
  });

  it('allows adding line items and calculates line totals correctly', () => {
    render(<POCreateForm />);
    
    // Initially one line item is present
    expect(screen.getAllByPlaceholderText('Description')).toHaveLength(1);
    
    // Add another item
    fireEvent.click(screen.getByRole('button', { name: /Add Item/i }));
    
    const descriptions = screen.getAllByPlaceholderText('Description');
    const quantities = screen.getAllByPlaceholderText('Qty');
    const unitPrices = screen.getAllByPlaceholderText('Price');

    expect(descriptions).toHaveLength(2);
    
    // Test calculation on first item
    fireEvent.change(quantities[0], { target: { value: '2' } });
    fireEvent.change(unitPrices[0], { target: { value: '50' } });
    
    // Find the total via value checking (since the input might not have an explicit label that easily matches)
    // The component calculates total and displays it in a read-only input or standard input
    // The read-only value for line total should now be '100.00'
    const totals = screen.getAllByText('100.00');
    expect(totals.length).toBeGreaterThan(0);
  });

  it('submits the expected PO payload and handles success', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (apiClient.purchaseOrders.create as any).mockResolvedValueOnce({ id: 'po-123' });

    render(<POCreateForm />);
    
    fireEvent.change(screen.getByPlaceholderText(/e.g. PO-1001/i), { target: { value: 'PO-TEST' } });
    fireEvent.change(screen.getByPlaceholderText(/e.g. Acme Supplies/i), { target: { value: 'Acme Corp' } });
    
    // First line item (default)
    const descriptions = screen.getAllByPlaceholderText('Description');
    const quantities = screen.getAllByPlaceholderText('Qty');
    const unitPrices = screen.getAllByPlaceholderText('Price');
    
    fireEvent.change(descriptions[0], { target: { value: 'Test Item' } });
    fireEvent.change(quantities[0], { target: { value: '5' } });
    fireEvent.change(unitPrices[0], { target: { value: '10' } });

    fireEvent.click(screen.getByRole('button', { name: /Create PO/i }));
    
    const successMsg = await screen.findByText('Purchase order created successfully!');
    expect(successMsg).toBeInTheDocument();
    
    expect(apiClient.purchaseOrders.create).toHaveBeenCalledTimes(1);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const callArgs = (apiClient.purchaseOrders.create as any).mock.calls[0][0];
    expect(callArgs.po_number).toBe('PO-TEST');
    expect(mockRefresh).toHaveBeenCalledTimes(1);
  });

  it('handles API failure correctly', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (apiClient.purchaseOrders.create as any).mockRejectedValueOnce(new Error('Vendor not allowed'));

    render(<POCreateForm />);
    
    fireEvent.change(screen.getByPlaceholderText(/e.g. PO-1001/i), { target: { value: 'PO-FAIL' } });
    fireEvent.change(screen.getByPlaceholderText(/e.g. Acme Supplies/i), { target: { value: 'Bad Vendor' } });
    
    const descriptions = screen.getAllByPlaceholderText('Description');
    fireEvent.change(descriptions[0], { target: { value: 'Bad Item' } });
    
    fireEvent.click(screen.getByRole('button', { name: /Create PO/i }));

    const errorMsg = await screen.findByText('Vendor not allowed');
    expect(errorMsg).toBeInTheDocument();
  });
});
