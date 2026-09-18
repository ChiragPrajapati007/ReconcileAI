'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { apiClient } from '@/lib/api/client';

export default function POCreateForm() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const [poNumber, setPoNumber] = useState('');
  const [vendorName, setVendorName] = useState('');
  const [poDate, setPoDate] = useState(new Date().toISOString().split('T')[0]);
  
  const [items, setItems] = useState([
    { line_number: 1, description: '', quantity: '1', unit_price: '0', line_total: '0' }
  ]);

  const handleItemChange = (index: number, field: string, value: string) => {
    const newItems = [...items];
    const item = newItems[index] as unknown as Record<string, string>;
    item[field] = value;
    
    // Auto-calculate line total
    if (field === 'quantity' || field === 'unit_price') {
      const q = parseFloat(item.quantity) || 0;
      const u = parseFloat(item.unit_price) || 0;
      item.line_total = (q * u).toFixed(2);
    }
    setItems(newItems);
  };

  const addItem = () => {
    setItems([
      ...items, 
      { line_number: items.length + 1, description: '', quantity: '1', unit_price: '0', line_total: '0' }
    ]);
  };

  const removeItem = (index: number) => {
    if (items.length <= 1) return;
    const newItems = items.filter((_, i) => i !== index).map((item, i) => ({
      ...item,
      line_number: i + 1
    }));
    setItems(newItems);
  };

  const calculateTotal = () => {
    return items.reduce((sum, item) => sum + (parseFloat(item.line_total) || 0), 0).toFixed(2);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    // Client-side validation
    if (items.length === 0) {
      setError("Purchase order must have at least one line item.");
      return;
    }
    
    for (const item of items) {
      if (!item.description || item.description.trim() === '') {
        setError(`Description is required for line item ${item.line_number}.`);
        return;
      }
      const q = parseFloat(item.quantity);
      if (isNaN(q) || q <= 0) {
        setError(`Quantity must be greater than 0 for line item ${item.line_number}.`);
        return;
      }
      const u = parseFloat(item.unit_price);
      if (isNaN(u) || u < 0) {
        setError(`Unit price cannot be negative for line item ${item.line_number}.`);
        return;
      }
    }

    setLoading(true);
    setError(null);
    setSuccess(false);

    try {
      const total = calculateTotal();
      const payload = {
        po_number: poNumber,
        vendor_name: vendorName,
        po_date: new Date(poDate).toISOString(),
        currency: 'INR',
        subtotal: total,
        tax_amount: '0',
        discount_amount: '0',
        grand_total: total,
        items: items.map(i => ({
          line_number: i.line_number,
          description: i.description,
          quantity: parseFloat(i.quantity) || 0,
          unit_price: parseFloat(i.unit_price) || 0,
          tax_rate_percent: 0,
          discount: 0,
          line_total: parseFloat(i.line_total) || 0,
        }))
      };

      await apiClient.purchaseOrders.create(payload);
      setSuccess(true);
      setPoNumber('');
      setVendorName('');
      setItems([{ line_number: 1, description: '', quantity: '1', unit_price: '0', line_total: '0' }]);
      router.refresh();
      
      setTimeout(() => setSuccess(false), 3000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to create purchase order');
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && (
        <div className="bg-red-50 text-red-600 p-3 rounded text-sm border border-red-200">
          {error}
        </div>
      )}
      
      {success && (
        <div className="bg-emerald-50 text-emerald-700 p-3 rounded text-sm border border-emerald-200">
          Purchase order created successfully!
        </div>
      )}

      <div>
        <label className="block text-xs font-semibold text-gray-700 mb-1">PO Number</label>
        <input 
          type="text" 
          required 
          value={poNumber}
          onChange={(e) => setPoNumber(e.target.value)}
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500" 
          placeholder="e.g. PO-1001"
        />
      </div>

      <div>
        <label className="block text-xs font-semibold text-gray-700 mb-1">Vendor Name</label>
        <input 
          type="text" 
          required 
          value={vendorName}
          onChange={(e) => setVendorName(e.target.value)}
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500" 
          placeholder="e.g. Acme Supplies"
        />
      </div>

      <div>
        <label className="block text-xs font-semibold text-gray-700 mb-1">PO Date</label>
        <input 
          type="date" 
          required 
          value={poDate}
          onChange={(e) => setPoDate(e.target.value)}
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500" 
        />
      </div>

      <div className="pt-2 border-t border-gray-200">
        <div className="flex justify-between items-center mb-2">
          <label className="block text-xs font-semibold text-gray-700">Line Items</label>
          <button 
            type="button" 
            onClick={addItem}
            className="text-xs text-blue-600 hover:text-blue-800 font-medium"
          >
            + Add Item
          </button>
        </div>

        <div className="space-y-2">
          {items.map((item, index) => (
            <div key={index} className="flex gap-2 items-start bg-gray-50 p-2 rounded border border-gray-200">
              <div className="flex-1 space-y-2">
                <input 
                  type="text" 
                  required 
                  value={item.description}
                  onChange={(e) => handleItemChange(index, 'description', e.target.value)}
                  className="w-full border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none" 
                  placeholder="Description"
                />
                <div className="flex gap-2">
                  <div className="w-1/3">
                    <input 
                      type="number" 
                      min="0.01" step="0.01" required 
                      value={item.quantity}
                      onChange={(e) => handleItemChange(index, 'quantity', e.target.value)}
                      className="w-full border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none" 
                      placeholder="Qty"
                    />
                  </div>
                  <div className="w-1/3">
                    <input 
                      type="number" 
                      min="0" step="0.01" required 
                      value={item.unit_price}
                      onChange={(e) => handleItemChange(index, 'unit_price', e.target.value)}
                      className="w-full border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none" 
                      placeholder="Price"
                    />
                  </div>
                  <div className="w-1/3 flex items-center justify-end px-2 text-sm font-medium text-gray-700">
                    {item.line_total}
                  </div>
                </div>
              </div>
              <button 
                type="button" 
                onClick={() => removeItem(index)}
                disabled={items.length === 1}
                className="text-gray-400 hover:text-red-600 px-1 disabled:opacity-50"
                title="Remove item"
              >
                &times;
              </button>
            </div>
          ))}
        </div>
      </div>

      <div className="flex justify-between items-center pt-4 border-t border-gray-200">
        <span className="font-semibold text-gray-900">Total: {calculateTotal()}</span>
        <button 
          type="submit" 
          disabled={loading}
          className="bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded text-sm disabled:opacity-50 transition-colors"
        >
          {loading ? 'Creating...' : 'Create PO'}
        </button>
      </div>
    </form>
  );
}
