import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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

  it('renders the PO form fields', () => {
    render(<POCreateForm />);
    expect(screen.getByText('PO Number')).toBeInTheDocument();
    expect(screen.getByText('Vendor Name')).toBeInTheDocument();
    expect(screen.getByText('PO Date')).toBeInTheDocument();
  });

  it('validates empty description on submit', async () => {
    render(<POCreateForm />);
    
    fireEvent.change(screen.getByPlaceholderText('e.g. PO-1001'), { target: { value: 'PO-123' } });
    fireEvent.change(screen.getByPlaceholderText('e.g. Acme Supplies'), { target: { value: 'Acme' } });
    
    // Default description is empty, try submitting
    fireEvent.submit(screen.getByRole('button', { name: 'Create PO' }).closest('form')!);
    
    await waitFor(() => {
      expect(screen.getByText(/Description is required for line item/i)).toBeInTheDocument();
    });
    
    expect(apiClient.purchaseOrders.create).not.toHaveBeenCalled();
  });

  it('validates negative unit price on submit', async () => {
    render(<POCreateForm />);
    
    fireEvent.change(screen.getByPlaceholderText('e.g. PO-1001'), { target: { value: 'PO-123' } });
    fireEvent.change(screen.getByPlaceholderText('e.g. Acme Supplies'), { target: { value: 'Acme' } });
    fireEvent.change(screen.getByPlaceholderText('Description'), { target: { value: 'Widgets' } });
    fireEvent.change(screen.getByPlaceholderText('Price'), { target: { value: '-10.50' } });
    
    fireEvent.submit(screen.getByRole('button', { name: 'Create PO' }).closest('form')!);
    
    await waitFor(() => {
      expect(screen.getByText(/Unit price cannot be negative/i)).toBeInTheDocument();
    });
    
    expect(apiClient.purchaseOrders.create).not.toHaveBeenCalled();
  });

  it('submits successfully and calls router.refresh()', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (apiClient.purchaseOrders.create as any).mockResolvedValueOnce({ id: 'po-1' });

    render(<POCreateForm />);
    
    fireEvent.change(screen.getByPlaceholderText('e.g. PO-1001'), { target: { value: 'PO-123' } });
    fireEvent.change(screen.getByPlaceholderText('e.g. Acme Supplies'), { target: { value: 'Acme' } });
    fireEvent.change(screen.getByPlaceholderText('Description'), { target: { value: 'Widgets' } });
    fireEvent.change(screen.getByPlaceholderText('Qty'), { target: { value: '10' } });
    fireEvent.change(screen.getByPlaceholderText('Price'), { target: { value: '5.00' } });
    
    fireEvent.submit(screen.getByRole('button', { name: 'Create PO' }).closest('form')!);
    
    await waitFor(() => {
      expect(apiClient.purchaseOrders.create).toHaveBeenCalled();
      expect(mockRefresh).toHaveBeenCalled();
      expect(screen.getByText(/Purchase order created successfully!/i)).toBeInTheDocument();
    });
  });
});
