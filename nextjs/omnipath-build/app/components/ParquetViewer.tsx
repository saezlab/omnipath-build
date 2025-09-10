'use client';

import React, { useState, useEffect } from 'react';
import { parquetMetadata, parquetReadObjects } from 'hyparquet';
import { compressors } from 'hyparquet-compressors';
import { ChevronLeft, ChevronRight, Download, Info } from 'lucide-react';

interface ParquetViewerProps {
  filePath: string;
  fileData?: ArrayBuffer;
}

export default function ParquetViewer({ filePath, fileData }: ParquetViewerProps) {
  const [metadata, setMetadata] = useState<any>(null);
  const [data, setData] = useState<any[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
  const [currentPage, setCurrentPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  const rowsPerPage = 20;

  useEffect(() => {
    if (!fileData) return;
    
    const loadData = async () => {
      try {
        setLoading(true);
        setError(null);
        
        const meta = parquetMetadata(fileData);
        setMetadata(meta);
        
        const cols = meta.schema
          .filter((s: any) => s.name !== 'schema')
          .map((s: any) => s.name);
        setColumns(cols);
        
        const rows = await parquetReadObjects({
          file: fileData,
          columns: cols,
          rowEnd: 1000,
          compressors
        });
        
        setData(rows);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load parquet file');
      } finally {
        setLoading(false);
      }
    };

    loadData();
  }, [fileData]);

  const totalPages = Math.ceil(data.length / rowsPerPage);
  const startIndex = (currentPage - 1) * rowsPerPage;
  const endIndex = startIndex + rowsPerPage;
  const currentData = data.slice(startIndex, endIndex);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-4">
        <p className="text-red-600 dark:text-red-400">Error loading file: {error}</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
            {filePath.split('/').pop()}
          </h3>
          <div className="flex items-center gap-2">
            <button className="p-2 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors">
              <Info className="w-4 h-4" />
            </button>
            <button className="p-2 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors">
              <Download className="w-4 h-4" />
            </button>
          </div>
        </div>

        {metadata && (
          <div className="grid grid-cols-3 gap-4 mb-4 text-sm">
            <div>
              <span className="text-gray-500 dark:text-gray-400">Rows:</span>
              <span className="ml-2 font-medium">{metadata.num_rows.toLocaleString()}</span>
            </div>
            <div>
              <span className="text-gray-500 dark:text-gray-400">Columns:</span>
              <span className="ml-2 font-medium">{columns.length}</span>
            </div>
            <div>
              <span className="text-gray-500 dark:text-gray-400">Size:</span>
              <span className="ml-2 font-medium">{(fileData!.byteLength / 1024 / 1024).toFixed(2)} MB</span>
            </div>
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead className="bg-gray-50 dark:bg-gray-900">
              <tr>
                {columns.map((col, colIndex) => (
                  <th
                    key={`${col}-${colIndex}`}
                    className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider"
                  >
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
              {currentData.map((row, rowIndex) => (
                <tr key={`row-${startIndex + rowIndex}`} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                  {columns.map((col, colIndex) => (
                    <td key={`${rowIndex}-${col}-${colIndex}`} className="px-6 py-4 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100">
                      {row[col]?.toString() || '-'}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="flex items-center justify-between mt-4">
          <div className="text-sm text-gray-700 dark:text-gray-300">
            Showing {startIndex + 1} to {Math.min(endIndex, data.length)} of {data.length} rows
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setCurrentPage(Math.max(1, currentPage - 1))}
              disabled={currentPage === 1}
              className="p-2 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className="px-3 py-1 text-sm">
              Page {currentPage} of {totalPages}
            </span>
            <button
              onClick={() => setCurrentPage(Math.min(totalPages, currentPage + 1))}
              disabled={currentPage === totalPages}
              className="p-2 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
