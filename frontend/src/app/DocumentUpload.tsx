'use client';

import { useState, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { apiClient } from '@/lib/api/client';

export default function DocumentUpload() {
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement>(null);
  
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      const selectedFile = e.target.files[0];
      
      // Validation
      if (selectedFile.size > 20 * 1024 * 1024) {
        setError("File exceeds 20MB limit.");
        setFile(null);
        return;
      }
      
      if (!selectedFile.type.startsWith('image/') && selectedFile.type !== 'application/pdf') {
        setError("Only PDF and image files are supported.");
        setFile(null);
        return;
      }

      setFile(selectedFile);
      setError(null);
    }
  };

  const handleUpload = async () => {
    if (!file) return;
    setLoading(true);
    setError(null);

    try {
      const response = await apiClient.extraction.ingest(file);
      
      // Navigate to the reconciliation workspace if ingestion passes the AUTO gate
      if (response.gate_status === 'AUTO') {
        router.push(`/invoices/${response.invoice_id}`);
      } else {
        // If it got blocked or needs review, we still navigate to the workspace 
        // which will show the appropriate state.
        router.push(`/invoices/${response.invoice_id}`);
      }
    } catch (err: unknown) {
      console.error(err);
      setError(err instanceof Error ? err.message : 'Failed to ingest document');
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      
      <div 
        className={`border-2 border-dashed rounded-lg p-6 text-center ${file ? 'border-blue-400 bg-blue-50' : 'border-gray-300 hover:border-gray-400'}`}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            const selectedFile = e.dataTransfer.files[0];
            
            // Validation
            if (selectedFile.size > 20 * 1024 * 1024) {
              setError("File exceeds 20MB limit.");
              setFile(null);
              return;
            }
            
            if (!selectedFile.type.startsWith('image/') && selectedFile.type !== 'application/pdf') {
              setError("Only PDF and image files are supported.");
              setFile(null);
              return;
            }

            setFile(selectedFile);
            setError(null);
          }
        }}
      >
        <input 
          type="file" 
          ref={fileInputRef} 
          onChange={handleFileChange} 
          accept="application/pdf,image/*" 
          className="hidden" 
        />
        
        {!file ? (
          <div className="space-y-2 cursor-pointer" onClick={() => fileInputRef.current?.click()}>
            <svg className="mx-auto h-8 w-8 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
            </svg>
            <div className="text-sm text-gray-600">
              <span className="font-medium text-blue-600 hover:text-blue-500">Click to upload</span> or drag and drop
            </div>
            <p className="text-xs text-gray-500">PDF or Images up to 10MB</p>
          </div>
        ) : (
          <div className="space-y-2">
            <svg className="mx-auto h-8 w-8 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <div className="text-sm font-medium text-gray-900 truncate px-2">
              {file.name}
            </div>
            <button 
              onClick={() => setFile(null)}
              className="text-xs text-red-600 hover:text-red-800 font-medium"
            >
              Remove
            </button>
          </div>
        )}
      </div>

      {error && (
        <div className="bg-red-50 text-red-600 p-3 rounded text-sm border border-red-200">
          {error}
        </div>
      )}

      <button
        onClick={handleUpload}
        disabled={!file || loading}
        className={`w-full py-2.5 px-4 rounded font-medium text-white shadow-sm flex justify-center items-center gap-2 transition-colors ${
          !file || loading 
            ? 'bg-blue-400 cursor-not-allowed' 
            : 'bg-blue-600 hover:bg-blue-700'
        }`}
      >
        {loading ? (
          <>
            <svg className="animate-spin h-4 w-4 text-white" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span>Processing Extraction...</span>
          </>
        ) : (
          <span>Run Autonomous Ingestion &rarr;</span>
        )}
      </button>

    </div>
  );
}
