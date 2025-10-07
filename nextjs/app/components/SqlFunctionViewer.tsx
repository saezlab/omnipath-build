'use client';

import { useState, useEffect } from 'react';
import Prism from 'prismjs';
import 'prismjs/components/prism-sql';
import 'prismjs/themes/prism-tomorrow.css';

interface SqlFunction {
  name: string;
  params: string;
  body: string;
  fullText: string;
}

export default function SqlFunctionViewer() {
  const [functions, setFunctions] = useState<SqlFunction[]>([]);
  const [selectedFunction, setSelectedFunction] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');

  useEffect(() => {
    setLoading(true);
    fetch('/api/config/transform-functions')
      .then(res => res.json())
      .then(data => {
        setFunctions(data.functions || []);
        if (data.functions && data.functions.length > 0) {
          setSelectedFunction(data.functions[0].name);
        }
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  useEffect(() => {
    Prism.highlightAll();
  }, [selectedFunction]);

  const filteredFunctions = functions.filter(fn =>
    fn.name.toLowerCase().includes(searchTerm.toLowerCase())
  );

  if (loading) {
    return <div className="p-4">Loading...</div>;
  }

  const currentFunction = functions.find(fn => fn.name === selectedFunction);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <label className="font-medium">Function:</label>
          <select
            value={selectedFunction}
            onChange={(e) => setSelectedFunction(e.target.value)}
            className="px-3 py-1 border rounded bg-white"
          >
            {filteredFunctions.map(fn => (
              <option key={fn.name} value={fn.name}>{fn.name}</option>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-2 flex-1">
          <label className="font-medium">Search:</label>
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Filter functions..."
            className="px-3 py-1 border rounded flex-1 max-w-xs"
          />
        </div>
      </div>

      {currentFunction && (
        <div className="border rounded p-4 space-y-3 bg-gray-50">
          <div>
            <strong>Function Name:</strong> <span className="font-mono text-blue-600">{currentFunction.name}</span>
          </div>

          {currentFunction.params && (
            <div>
              <strong>Parameters:</strong> <span className="font-mono text-sm">{currentFunction.params}</span>
            </div>
          )}

          <div>
            <h4 className="font-semibold mb-2">SQL Definition:</h4>
            <pre className="rounded overflow-x-auto"><code className="language-sql">{currentFunction.fullText}</code></pre>
          </div>
        </div>
      )}

      {filteredFunctions.length === 0 && (
        <div className="text-gray-500 text-center py-8">
          No functions found matching &quot;{searchTerm}&quot;
        </div>
      )}
    </div>
  );
}
