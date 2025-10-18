'use client';

import { useState, useEffect } from 'react';
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbList,
  BreadcrumbPage,
} from "@/components/ui/breadcrumb";
import { ParquetViewer } from '@/components/parquet-viewer';
import { useDatabase } from "@/hooks/use-database-context";

type ViewMode = 'gold' | 'silver';

interface GoldTable {
  name: string;
  path: string;
  size: number;
  modified: Date;
}

export default function DashboardPage() {
  const { sources, loading } = useDatabase();
  const [viewMode, setViewMode] = useState<ViewMode>('gold');
  const [activeGoldTable, setActiveGoldTable] = useState<string>('');
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [parquetFile, setParquetFile] = useState<File | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [goldTables, setGoldTables] = useState<GoldTable[]>([]);

  // Get all silver files from all sources
  const getAllSilverFiles = () => {
    const allFiles: Array<{ file: any; sourceName: string }> = [];
    sources.forEach(source => {
      const files = source.layers.silver || [];
      files.forEach(file => {
        allFiles.push({ file, sourceName: source.name });
      });
    });
    return allFiles;
  };

  // Fetch gold tables
  useEffect(() => {
    const fetchGoldTables = async () => {
      try {
        const response = await fetch('/api/sources');
        if (!response.ok) throw new Error('Failed to fetch data');
        const data = await response.json();
        setGoldTables(data.combinedTables || []);
      } catch (error) {
        console.error('Error fetching gold tables:', error);
      }
    };
    fetchGoldTables();
  }, []);

  // Set initial gold table
  useEffect(() => {
    if (goldTables.length > 0 && !activeGoldTable) {
      setActiveGoldTable(goldTables[0].name);
    }
  }, [goldTables, activeGoldTable]);

  // Auto-load table when switching tabs in gold mode
  useEffect(() => {
    if (viewMode === 'gold' && activeGoldTable) {
      const table = goldTables.find(t => t.name === activeGoldTable);
      if (table) {
        handleFileSelect(table.path);
      }
    }
  }, [activeGoldTable, viewMode, goldTables]);

  // Auto-load first file when switching to silver mode
  useEffect(() => {
    if (viewMode === 'silver') {
      const allFiles = getAllSilverFiles();
      if (allFiles.length > 0) {
        handleFileSelect(allFiles[0].file.path);
      } else {
        setSelectedFile(null);
        setParquetFile(null);
      }
    }
  }, [viewMode, sources]);

  const handleFileSelect = async (path: string) => {
    setSelectedFile(path);
    setFileLoading(true);
    try {
      const response = await fetch(`/api/parquet?path=${encodeURIComponent(path)}`);
      if (!response.ok) throw new Error('Failed to load file');
      const data = await response.arrayBuffer();
      // Convert ArrayBuffer to File object for the new ParquetViewer
      const fileName = path.split('/').pop() || 'data.parquet';
      const file = new File([data], fileName, { type: 'application/octet-stream' });
      setParquetFile(file);
    } catch (error) {
      console.error('Error loading file:', error);
    } finally {
      setFileLoading(false);
    }
  };

  if (loading) {
    return (
      <>
        <header className="flex h-16 shrink-0 items-center gap-2">
          <div className="flex items-center gap-2 px-4">
            <SidebarTrigger className="-ml-1" />
            <Separator orientation="vertical" className="mr-2 h-4" />
            <Breadcrumb>
              <BreadcrumbList>
                <BreadcrumbItem>
                  <BreadcrumbPage>Dashboard</BreadcrumbPage>
                </BreadcrumbItem>
              </BreadcrumbList>
            </Breadcrumb>
          </div>
        </header>
        <div className="flex flex-1 flex-col gap-4 p-4 pt-0">
          <div className="flex items-center justify-center h-64">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
          </div>
        </div>
      </>
    );
  }

  const currentGoldTable = goldTables.find(t => t.name === activeGoldTable);
  const allSilverFiles = getAllSilverFiles();

  return (
    <>
      <header className="flex h-16 shrink-0 items-center gap-2">
        <div className="flex items-center gap-2 px-4 w-full">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-2 h-4" />
          <Breadcrumb>
            <BreadcrumbList>
              <BreadcrumbItem>
                <BreadcrumbPage>Dashboard</BreadcrumbPage>
              </BreadcrumbItem>
            </BreadcrumbList>
          </Breadcrumb>

          {/* Mode Toggle */}
          <div className="ml-auto flex gap-2">
            <button
              onClick={() => {
                setViewMode('gold');
                setSelectedFile(null);
                setParquetFile(null);
              }}
              className={`px-4 py-2 rounded-lg font-medium transition-colors ${
                viewMode === 'gold'
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-300 dark:hover:bg-gray-600'
              }`}
            >
              Gold (Combined)
            </button>
            <button
              onClick={() => {
                setViewMode('silver');
                setSelectedFile(null);
                setParquetFile(null);
              }}
              className={`px-4 py-2 rounded-lg font-medium transition-colors ${
                viewMode === 'silver'
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-300 dark:hover:bg-gray-600'
              }`}
            >
              Silver (Source-Specific)
            </button>
          </div>
        </div>
      </header>

      <div className="flex flex-1 flex-col gap-4 p-4 pt-0">
        {viewMode === 'gold' ? (
          <>
            {/* Gold Layer - Combined Tables */}
            <div className="mb-4">
              <h1 className="text-3xl font-bold text-gray-900 dark:text-white">
                Gold Layer - Combined Tables
              </h1>
              <p className="mt-1 text-gray-600 dark:text-gray-400">
                Final integrated data from all sources
              </p>
            </div>

            {/* Table Tabs */}
            <div className="border-b border-gray-200 dark:border-gray-700">
              <nav className="-mb-px flex space-x-4 overflow-x-auto">
                {goldTables.map((table) => (
                  <button
                    key={table.name}
                    onClick={() => {
                      setActiveGoldTable(table.name);
                      setSelectedFile(null);
                      setParquetFile(null);
                    }}
                    className={`
                      whitespace-nowrap py-3 px-3 border-b-2 font-medium text-sm
                      ${activeGoldTable === table.name
                        ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                        : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300 dark:text-gray-400 dark:hover:text-gray-300'
                      }
                    `}
                  >
                    {table.name}
                  </button>
                ))}
              </nav>
            </div>

            {/* Table View */}
            <div className="flex-1">
              {fileLoading ? (
                <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-8 flex items-center justify-center" style={{ minHeight: '500px' }}>
                  <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
                </div>
              ) : parquetFile ? (
                <ParquetViewer file={parquetFile} />
              ) : goldTables.length === 0 ? (
                <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-8 h-full flex items-center justify-center">
                  <div className="text-center">
                    <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-2">
                      No gold tables available
                    </h3>
                    <p className="text-sm text-gray-500 dark:text-gray-400">
                      Check the omnipath_build/gold_duckdb/ directory
                    </p>
                  </div>
                </div>
              ) : null}
            </div>
          </>
        ) : (
          <>
            {/* Silver Layer - Source-Specific */}
            <div className="mb-4">
              <h1 className="text-3xl font-bold text-gray-900 dark:text-white">
                Silver Layer - Source-Specific Data
              </h1>
              <p className="mt-1 text-gray-600 dark:text-gray-400">
                Processed data from individual sources
              </p>
            </div>

            {/* File Grid */}
            <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 flex-1">
              <div className="lg:col-span-1">
                <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 h-full overflow-y-auto">
                  <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4 sticky top-0 bg-white dark:bg-gray-800 pb-2">
                    Silver Files ({allSilverFiles.length})
                  </h3>
                  <div className="space-y-2">
                    {allSilverFiles.map(({ file, sourceName }) => (
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
                        <div className="font-medium">{file.name}</div>
                        <div className="text-xs text-gray-500 dark:text-gray-400">
                          {sourceName} • {(file.size / 1024).toFixed(2)} KB
                        </div>
                      </button>
                    ))}
                    {allSilverFiles.length === 0 && (
                      <p className="text-sm text-gray-500 dark:text-gray-400 text-center py-4">
                        No silver files available
                      </p>
                    )}
                  </div>
                </div>
              </div>

              <div className="lg:col-span-3">
                {fileLoading ? (
                  <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-8 h-full flex items-center justify-center">
                    <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
                  </div>
                ) : parquetFile ? (
                  <ParquetViewer file={parquetFile} />
                ) : (
                  <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-8 h-full flex items-center justify-center">
                    <div className="text-center">
                      <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-2">
                        Select a file to view
                      </h3>
                      <p className="text-sm text-gray-500 dark:text-gray-400">
                        Choose a silver file from the list
                      </p>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </>
  );
}
