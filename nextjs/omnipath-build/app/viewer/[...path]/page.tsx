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
    <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100 dark:from-gray-900 dark:to-gray-950">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="mb-8">
          <Link 
            href={`/database/${dbName}`}
            className="inline-flex items-center gap-2 text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white mb-4"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to {dbName}
          </Link>
          
          <h1 className="text-3xl font-bold text-gray-900 dark:text-white">
            Parquet File Viewer
          </h1>
          <p className="mt-2 text-gray-600 dark:text-gray-400">
            {filePath}
          </p>
        </div>

        {loading ? (
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-8">
            <div className="flex items-center justify-center">
              <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
            </div>
          </div>
        ) : error ? (
          <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-4">
            <p className="text-red-600 dark:text-red-400">Error: {error}</p>
          </div>
        ) : fileData ? (
          <ParquetViewer filePath={filePath} fileData={fileData} />
        ) : null}
      </div>
    </div>
  );
}
