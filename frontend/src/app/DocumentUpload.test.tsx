import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import DocumentUpload from './DocumentUpload';
import { apiClient } from '@/lib/api/client';
import { useRouter } from 'next/navigation';

// Mock next/navigation
vi.mock('next/navigation', () => ({
  useRouter: vi.fn(),
}));

// Mock API client
vi.mock('@/lib/api/client', () => ({
  apiClient: {
    extraction: {
      ingest: vi.fn(),
    },
  },
}));

describe('DocumentUpload Component', () => {
  const mockPush = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (useRouter as any).mockReturnValue({ push: mockPush });
  });

  it('renders the upload interface initially', () => {
    render(<DocumentUpload />);
    
    expect(screen.getByText(/Click to upload/i)).toBeInTheDocument();
    expect(screen.getByText(/drag and drop/i)).toBeInTheDocument();
  });

  it('accepts a file selection', () => {
    render(<DocumentUpload />);
    
    const file = new File(['dummy content'], 'invoice.pdf', { type: 'application/pdf' });
    const input = document.querySelector('input[type="file"]')!;
    
    fireEvent.change(input, { target: { files: [file] } });
    
    expect(screen.getByText('invoice.pdf')).toBeInTheDocument();
    expect(screen.getByText('Remove')).toBeInTheDocument();
  });

  it('shows loading state while ingestion is running and navigates on success', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (apiClient.extraction.ingest as any).mockResolvedValueOnce({
      invoice_id: '123-abc',
      gate_status: 'AUTO'
    });

    render(<DocumentUpload />);
    
    const file = new File(['dummy'], 'invoice.pdf', { type: 'application/pdf' });
    const input = document.querySelector('input[type="file"]')!;
    
    fireEvent.change(input, { target: { files: [file] } });
    
    const uploadBtn = screen.getByRole('button', { name: /Run Autonomous Ingestion/i });
    fireEvent.click(uploadBtn);
    
    expect(uploadBtn).toHaveTextContent(/Processing Extraction/i);
    expect(uploadBtn).toBeDisabled();

    await waitFor(() => {
      expect(apiClient.extraction.ingest).toHaveBeenCalledWith(file);
      expect(mockPush).toHaveBeenCalledWith('/invoices/123-abc');
    });
  });

  it('displays an appropriate error state when ingestion fails', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (apiClient.extraction.ingest as any).mockRejectedValueOnce(new Error('Network failure'));

    render(<DocumentUpload />);
    
    const file = new File(['dummy'], 'invoice.pdf', { type: 'application/pdf' });
    const input = document.querySelector('input[type="file"]')!;
    
    fireEvent.change(input, { target: { files: [file] } });
    
    fireEvent.click(screen.getByRole('button', { name: /Run Autonomous Ingestion/i }));

    await waitFor(() => {
      expect(screen.getByText('Network failure')).toBeInTheDocument();
    });
  });

  it('rejects files larger than 20MB', async () => {
    render(<DocumentUpload />);
    
    // Create a dummy file and mock its size
    const file = new File(['dummy'], 'large.pdf', { type: 'application/pdf' });
    Object.defineProperty(file, 'size', { value: 25 * 1024 * 1024 }); // 25MB
    
    const input = document.querySelector('input[type="file"]')!;
    fireEvent.change(input, { target: { files: [file] } });
    
    await waitFor(() => {
      expect(screen.getByText('File exceeds 20MB limit.')).toBeInTheDocument();
    });
  });

  it('rejects unsupported file types', async () => {
    render(<DocumentUpload />);
    
    const file = new File(['dummy'], 'document.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
    
    const input = document.querySelector('input[type="file"]')!;
    fireEvent.change(input, { target: { files: [file] } });
    
    await waitFor(() => {
      expect(screen.getByText('Only PDF and image files are supported.')).toBeInTheDocument();
    });
  });
});
