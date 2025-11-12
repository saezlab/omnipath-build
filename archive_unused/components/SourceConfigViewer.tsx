'use client';

import { useState, useEffect } from 'react';
import Prism from 'prismjs';
import 'prismjs/components/prism-sql';
import 'prismjs/themes/prism-tomorrow.css';

interface FieldMapping {
  source: string | string[];
  target: string;
  value?: string;
  transform?: string;
}

interface FunctionConfig {
  processing: {
    target_table: string;
    field_mapping: FieldMapping[];
  };
}

interface SourceConfig {
  metadata: {
    name: string;
    description: string;
  };
  module: string;
  functions: {
    [key: string]: FunctionConfig;
  };
}

interface SqlFunction {
  name: string;
  params: string;
  body: string;
  fullText: string;
}

export default function SourceConfigViewer() {
  const [sources, setSources] = useState<string[]>([]);
  const [selectedSource, setSelectedSource] = useState<string>('');
  const [config, setConfig] = useState<SourceConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [sqlFunctions, setSqlFunctions] = useState<SqlFunction[]>([]);
  const [selectedFunction, setSelectedFunction] = useState<SqlFunction | null>(null);
  const [showDialog, setShowDialog] = useState(false);

  useEffect(() => {
    fetch('/api/config/sources')
      .then(res => res.json())
      .then(data => {
        setSources(data.sources || []);
        if (data.sources && data.sources.length > 0) {
          setSelectedSource(data.sources[0]);
        }
      });

    // Fetch SQL functions
    fetch('/api/config/transform-functions')
      .then(res => res.json())
      .then(data => {
        setSqlFunctions(data.functions || []);
      });
  }, []);

  useEffect(() => {
    if (selectedSource) {
      setLoading(true);
      fetch(`/api/config/source/${selectedSource}`)
        .then(res => res.json())
        .then(data => {
          setConfig(data.config as SourceConfig);
          setLoading(false);
        })
        .catch(() => setLoading(false));
    }
  }, [selectedSource]);

  useEffect(() => {
    if (showDialog) {
      Prism.highlightAll();
    }
  }, [showDialog, selectedFunction]);

  const formatSource = (source: string | string[]): string => {
    if (Array.isArray(source)) {
      return `[${source.join(', ')}]`;
    }
    return source;
  };

  const handleFunctionClick = (functionName: string) => {
    const func = sqlFunctions.find(f => f.name === functionName);
    if (func) {
      setSelectedFunction(func);
      setShowDialog(true);
    }
  };

  const formatTransform = (mapping: FieldMapping): JSX.Element => {
    if (mapping.value) {
      return <span>&quot;{mapping.value}&quot;</span>;
    }
    if (mapping.transform) {
      return (
        <button
          onClick={() => handleFunctionClick(mapping.transform!)}
          className="text-blue-600 hover:text-blue-800 hover:underline cursor-pointer"
        >
          {mapping.transform}()
        </button>
      );
    }
    return <span>-</span>;
  };

  if (loading || !config) {
    return <div className="p-4">Loading...</div>;
  }

  const functionEntries = Object.entries(config.functions);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <label className="font-medium">Source:</label>
        <select
          value={selectedSource}
          onChange={(e) => setSelectedSource(e.target.value)}
          className="px-3 py-1 border rounded bg-white"
        >
          {sources.map(source => (
            <option key={source} value={source}>{source.toUpperCase()}</option>
          ))}
        </select>
      </div>

      {functionEntries.map(([funcName, funcConfig]) => (
        <div key={funcName} className="border rounded p-4 space-y-3 bg-gray-50">
          <div className="space-y-1">
            <div><strong>Resource:</strong> {config.metadata.name}</div>
            <div><strong>Function:</strong> {funcName}</div>
            <div><strong>Module:</strong> {config.module}</div>
            <div><strong>Target Table:</strong> {funcConfig.processing.target_table}</div>
            {config.metadata.description && (
              <div className="text-sm text-gray-600">{config.metadata.description}</div>
            )}
          </div>

          <div>
            <h4 className="font-semibold mb-2">Field Mappings:</h4>
            <div className="overflow-x-auto">
              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr className="bg-gray-200">
                    <th className="border px-3 py-2 text-left">Source</th>
                    <th className="border px-3 py-2 text-left">Target</th>
                    <th className="border px-3 py-2 text-left">Transform</th>
                  </tr>
                </thead>
                <tbody>
                  {funcConfig.processing.field_mapping.map((mapping, idx) => (
                    <tr key={idx} className="bg-white hover:bg-gray-100">
                      <td className="border px-3 py-2 font-mono text-xs">
                        {formatSource(mapping.source)}
                      </td>
                      <td className="border px-3 py-2 font-mono text-xs">
                        {mapping.target}
                      </td>
                      <td className="border px-3 py-2 font-mono text-xs">
                        {formatTransform(mapping)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      ))}

      {/* SQL Function Dialog */}
      {showDialog && selectedFunction && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" onClick={() => setShowDialog(false)}>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl max-w-4xl w-full mx-4 max-h-[80vh] overflow-hidden" onClick={(e) => e.stopPropagation()}>
            <div className="border-b border-gray-200 dark:border-gray-700 px-6 py-4 flex justify-between items-center">
              <div>
                <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
                  SQL Function: <span className="font-mono text-blue-600">{selectedFunction.name}</span>
                </h3>
                {selectedFunction.params && (
                  <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                    Parameters: <span className="font-mono">{selectedFunction.params}</span>
                  </p>
                )}
              </div>
              <button
                onClick={() => setShowDialog(false)}
                className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
              >
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="p-6 overflow-y-auto max-h-[calc(80vh-100px)]">
              <pre className="rounded overflow-x-auto"><code className="language-sql">{selectedFunction.fullText}</code></pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
