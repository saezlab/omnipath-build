'use client';

import { useState, useEffect } from 'react';
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
} from "@/components/ui/breadcrumb";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import ParquetViewer from './components/ParquetViewer';
import { useDatabase } from "@/hooks/use-database-context";

type ViewMode = 'combined' | 'source-specific';
type Layer = 'bronze' | 'silver' | 'gold';

interface CombinedTable {
  name: string;
  path: string;
  size: number;
  modified: Date;
}

export default function DashboardPage() {
  const { sources, loading } = useDatabase();
  const [viewMode, setViewMode] = useState<ViewMode>('combined');
  const [selectedSource, setSelectedSource] = useState<string>('');
  const [activeLayer, setActiveLayer] = useState<Layer>('bronze');
  const [activeCombinedTable, setActiveCombinedTable] = useState<string>('');
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileData, setFileData] = useState<ArrayBuffer | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [combinedTables, setCombinedTables] = useState<CombinedTable[]>([]);

  // Helper functions
  const getSourceLayerFiles = (sourceName: string, layer: Layer) => {
    const source = sources.find(s => s.name === sourceName);
    if (!source) return [];
    return source.layers[layer] || [];
  };

  // Fetch combined tables
  useEffect(() => {
    const fetchCombinedTables = async () => {
      try {
        const response = await fetch('/api/sources');
        if (!response.ok) throw new Error('Failed to fetch data');
        const data = await response.json();
        setCombinedTables(data.combinedTables || []);
      } catch (error) {
        console.error('Error fetching combined tables:', error);
      }
    };
    fetchCombinedTables();
  }, []);

  useEffect(() => {
    if (combinedTables.length > 0 && !activeCombinedTable) {
      setActiveCombinedTable(combinedTables[0].name);
    }
  }, [combinedTables]);

  // Auto-load table when switching tabs in combined mode
  useEffect(() => {
    if (viewMode === 'combined' && activeCombinedTable) {
      const table = combinedTables.find(t => t.name === activeCombinedTable);
      if (table) {
        handleFileSelect(table.path);
      }
    }
  }, [activeCombinedTable, viewMode]);

  // Auto-load first file when switching source or layer in source-specific mode
  useEffect(() => {
    if (viewMode === 'source-specific' && selectedSource && activeLayer) {
      const files = getSourceLayerFiles(selectedSource, activeLayer);
      if (files.length > 0) {
        handleFileSelect(files[0].path);
      } else {
        setSelectedFile(null);
        setFileData(null);
      }
    }
  }, [selectedSource, activeLayer, viewMode]);

  useEffect(() => {
    if (sources.length > 0 && !selectedSource) {
      setSelectedSource(sources[0].name);
    }
  }, [sources]);

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

  // Get the current combined table
  const getCurrentCombinedTable = () => {
    return combinedTables.find(t => t.name === activeCombinedTable);
  };

  const currentSource = sources.find(s => s.name === selectedSource);
  const currentCombinedTable = getCurrentCombinedTable();
  const currentSourceFiles = getSourceLayerFiles(selectedSource, activeLayer);

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
                setViewMode('combined');
                setSelectedFile(null);
                setFileData(null);
              }}
              className={`px-4 py-2 rounded-lg font-medium transition-colors ${
                viewMode === 'combined'
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-300 dark:hover:bg-gray-600'
              }`}
            >
              Combined Tables
            </button>
            <button
              onClick={() => {
                setViewMode('source-specific');
                setSelectedFile(null);
                setFileData(null);
              }}
              className={`px-4 py-2 rounded-lg font-medium transition-colors ${
                viewMode === 'source-specific'
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-300 dark:hover:bg-gray-600'
              }`}
            >
              Source-Specific
            </button>
          </div>
        </div>
      </header>

      <div className="flex flex-1 flex-col gap-4 p-4 pt-0">
        {viewMode === 'combined' ? (
          <>
            {/* Combined Output Tables Mode */}
            <div className="mb-4">
              <h1 className="text-3xl font-bold text-gray-900 dark:text-white">
                Combined Output Tables
              </h1>
              <p className="mt-1 text-gray-600 dark:text-gray-400">
                View final combined data across all sources
              </p>
            </div>

            {/* Table Tabs */}
            <div className="border-b border-gray-200 dark:border-gray-700">
              <nav className="-mb-px flex space-x-4 overflow-x-auto">
                {combinedTables.map((table) => (
                  <button
                    key={table.name}
                    onClick={() => {
                      setActiveCombinedTable(table.name);
                      setSelectedFile(null);
                      setFileData(null);
                    }}
                    className={`
                      whitespace-nowrap py-3 px-3 border-b-2 font-medium text-sm
                      ${activeCombinedTable === table.name
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
              ) : selectedFile && fileData ? (
                <ParquetViewer filePath={selectedFile} fileData={fileData} />
              ) : combinedTables.length === 0 ? (
                <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-8 h-full flex items-center justify-center">
                  <div className="text-center">
                    <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-2">
                      No combined tables available
                    </h3>
                    <p className="text-sm text-gray-500 dark:text-gray-400">
                      Check the omnipath/output/ directory
                    </p>
                  </div>
                </div>
              ) : null}
            </div>
          </>
        ) : (
          <>
            {/* Source-Specific Mode */}
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h1 className="text-3xl font-bold text-gray-900 dark:text-white">
                  Source-Specific Processing
                </h1>
                <p className="mt-1 text-gray-600 dark:text-gray-400">
                  View Bronze → Silver → Gold pipeline for a specific source
                </p>
              </div>

              {/* Source Dropdown */}
              <Select value={selectedSource} onValueChange={(value) => {
                setSelectedSource(value);
                setSelectedFile(null);
                setFileData(null);
              }}>
                <SelectTrigger className="w-[280px]">
                  <SelectValue placeholder="Select source" />
                </SelectTrigger>
                <SelectContent>
                  {sources.map((source) => (
                    <SelectItem key={source.name} value={source.name}>
                      <span className="capitalize">{source.name.replace(/_/g, ' ')}</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Layer Tabs */}
            <div className="border-b border-gray-200 dark:border-gray-700">
              <nav className="-mb-px flex space-x-8">
                {(['bronze', 'silver', 'gold'] as Layer[]).map((layer) => {
                  const layerFiles = getSourceLayerFiles(selectedSource, layer);
                  return (
                    <button
                      key={layer}
                      onClick={() => {
                        setActiveLayer(layer);
                        setSelectedFile(null);
                        setFileData(null);
                      }}
                      className={`
                        whitespace-nowrap py-3 px-1 border-b-2 font-medium text-sm capitalize
                        ${activeLayer === layer
                          ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                          : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300 dark:text-gray-400 dark:hover:text-gray-300'
                        }
                      `}
                    >
                      {layer} ({layerFiles.length})
                    </button>
                  );
                })}
              </nav>
            </div>

            {/* File Grid */}
            <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 flex-1">
              <div className="lg:col-span-1">
                <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 h-full">
                  <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4 capitalize">
                    {activeLayer} Files ({currentSourceFiles.length})
                  </h3>
                  <div className="space-y-2">
                    {currentSourceFiles.map((file: any) => (
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
                          {(file.size / 1024).toFixed(2)} KB
                        </div>
                      </button>
                    ))}
                    {currentSourceFiles.length === 0 && (
                      <p className="text-sm text-gray-500 dark:text-gray-400 text-center py-4">
                        No {activeLayer} files
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
                ) : selectedFile && fileData ? (
                  <ParquetViewer filePath={selectedFile} fileData={fileData} />
                ) : (
                  <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-8 h-full flex items-center justify-center">
                    <div className="text-center">
                      <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-2">
                        Select a file to view
                      </h3>
                      <p className="text-sm text-gray-500 dark:text-gray-400">
                        Choose a {activeLayer} file from the list
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
