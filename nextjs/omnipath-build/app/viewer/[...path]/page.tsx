'use client';

import { useState, useEffect } from 'react';
import { ArrowLeft } from 'lucide-react';
import Link from 'next/link';
import ParquetViewer from '../../components/ParquetViewer';

export default function ViewerPage({ params }: { params: Promise<{ path: string[] }> }) {
  const [fileData, setFileData] = useState<ArrayBuffer | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filePath, setFilePath] = useState<string>('');
  const [dbName, setDbName] = useState<string>('');

  useEffect(() => {
    const loadFile = async () => {
      try {
        setLoading(true);
        const { path } = await params;
        const pathStr = '/' + path.join('/');
        setFilePath(pathStr);
        setDbName(path[0]);
        
        const response = await fetch(`/api/parquet?path=${encodeURIComponent(pathStr)}`);
        
        if (!response.ok) {
          throw new Error('Failed to load file');
        }
        
        const data = await response.arrayBuffer();
        setFileData(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load file');
      } finally {
        setLoading(false);
      }
    };

    loadFile();
  }, [params]);

  return (
    <div className="h-screen max-w-6xl mx-auto overflow-hidden">
        {loading ? (
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-8 h-full flex items-center justify-center m-4">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
          </div>
        ) : error ? (
          <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-4 m-4">
            <p className="text-red-600 dark:text-red-400">Error: {error}</p>
          </div>
        ) : fileData ? (
          <ParquetViewer filePath={filePath} fileData={fileData} />
        ) : null}
    </div>
  );
}
