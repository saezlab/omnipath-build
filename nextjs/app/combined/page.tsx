'use client';

import { useState, useEffect } from 'react';
import { ArrowLeft, Database, Layers } from 'lucide-react';
import Link from 'next/link';
import ParquetViewer from '../components/ParquetViewer';

interface GoldTable {
  name: string;
  files: Array<{
    path: string;
    source: string;
    size: number;
    modified: Date;
  }>;
}

export default function CombinedGoldPage() {
  const [sources, setSources] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTable, setActiveTable] = useState<string>('entity');
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileData, setFileData] = useState<ArrayBuffer | null>(null);
  const [fileLoading, setFileLoading] = useState(false);

  const goldTableNames = ['entity', 'entity_identifier', 'provenance', 'source', 'compound', 'cv_term', 'cv_namespace'];

  useEffect(() => {
    const fetchSources = async () => {
      try {
        const response = await fetch('/api/sources');
        if (!response.ok) throw new Error('Failed to fetch sources');

        const data = await response.json();
        setSources(data.sources);
      } catch (error) {
        console.error('Error fetching sources:', error);
      } finally {
        setLoading(false);
      }
    };

    fetchSources();
  }, []);

  const handleFileSelect = async (path: string) => {
    setSelectedFile(path);
    setFileLoading(true);
    try {
      const response = await fetch(`/api/parquet?path=${encodeURIComponent(path)}`);
      if (!response.ok) throw new Error('Failed to load file');

      const data = await response.arrayBuffer();
      setFileData(data);
    } catch (error) {
      console.error('Error loading file:', error);
    } finally {
      setFileLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100 dark:from-gray-900 dark:to-gray-950 p-8">
        <div className="max-w-7xl mx-auto">
          <div className="flex items-center justify-center h-64">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
          </div>
        </div>
      </div>
    );
  }

  // Group gold files by table name
  const goldTables: { [key: string]: GoldTable } = {};

  sources.forEach(source => {
    source.layers.gold.forEach((file: any) => {
      const fileName = file.name.replace('.parquet', '');
      if (!goldTables[fileName]) {
        goldTables[fileName] = {
          name: fileName,
          files: []
        };
      }
      goldTables[fileName].files.push({
        path: file.path,
        source: source.name,
        size: file.size,
        modified: file.modified
      });
    });
  });

  const availableTables = Object.keys(goldTables).sort();
  const currentTable = goldTables[activeTable] || { name: activeTable, files: [] };

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100 dark:from-gray-900 dark:to-gray-950">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="mb-8">
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-2 text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white mb-4"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Dashboard
          </Link>

          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-4xl font-bold text-gray-900 dark:text-white">
                Combined Gold Tables
              </h1>
              <p className="mt-2 text-gray-600 dark:text-gray-400">
                View gold layer tables across all sources in OmniPath database
              </p>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-8">
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-3 mb-2">
              <Database className="w-5 h-5 text-blue-600" />
              <span className="text-sm font-medium text-gray-600 dark:text-gray-400">Total Sources</span>
            </div>
            <p className="text-2xl font-bold text-gray-900 dark:text-white">{sources.length}</p>
          </div>

          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-3 mb-2">
              <Layers className="w-5 h-5 text-green-600" />
              <span className="text-sm font-medium text-gray-600 dark:text-gray-400">Gold Tables</span>
            </div>
            <p className="text-2xl font-bold text-gray-900 dark:text-white">{availableTables.length}</p>
          </div>

          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-3 mb-2">
              <Database className="w-5 h-5 text-purple-600" />
              <span className="text-sm font-medium text-gray-600 dark:text-gray-400">Current Table Files</span>
            </div>
            <p className="text-2xl font-bold text-gray-900 dark:text-white">{currentTable.files.length}</p>
          </div>
        </div>

        {/* Table Tabs */}
        <div className="mb-6">
          <div className="border-b border-gray-200 dark:border-gray-700">
            <nav className="-mb-px flex space-x-4 overflow-x-auto">
              {availableTables.map((tableName) => (
                <button
                  key={tableName}
                  onClick={() => {
                    setActiveTable(tableName);
                    setSelectedFile(null);
                    setFileData(null);
                  }}
                  className={`
                    whitespace-nowrap py-4 px-3 border-b-2 font-medium text-sm
                    ${activeTable === tableName
                      ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                      : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300 dark:text-gray-400 dark:hover:text-gray-300'
                    }
                  `}
                >
                  {tableName} ({goldTables[tableName].files.length})
                </button>
              ))}
            </nav>
          </div>
        </div>

        {/* File Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-1">
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
              <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">
                {activeTable} Files
              </h3>
              <div className="space-y-2">
                {currentTable.files.map((file) => (
                  <button
                    key={file.path}
                    onClick={() => handleFileSelect(file.path)}
                    className={`
                      w-full text-left px-3 py-2 rounded-lg text-sm transition-colors
                      ${selectedFile === file.path
                        ? 'bg-blue-100 dark:bg-blue-900 text-blue-900 dark:text-blue-100'
                        : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300'
                      }
                    `}
                  >
                    <div className="font-medium capitalize">{file.source.replace(/_/g, ' ')}</div>
                    <div className="text-xs text-gray-500 dark:text-gray-400">
                      {(file.size / 1024).toFixed(2)} KB
                    </div>
                  </button>
                ))}
                {currentTable.files.length === 0 && (
                  <p className="text-sm text-gray-500 dark:text-gray-400 text-center py-4">
                    No files for this table
                  </p>
                )}
              </div>
            </div>
          </div>

          <div className="lg:col-span-2">
            {fileLoading ? (
              <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-8">
                <div className="flex items-center justify-center">
                  <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
                </div>
              </div>
            ) : selectedFile && fileData ? (
              <ParquetViewer filePath={selectedFile} fileData={fileData} />
            ) : (
              <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-8">
                <div className="text-center">
                  <Layers className="w-12 h-12 text-gray-400 mx-auto mb-4" />
                  <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-2">
                    Select a file to view
                  </h3>
                  <p className="text-sm text-gray-500 dark:text-gray-400">
                    Choose a source from the list to explore its {activeTable} table
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
