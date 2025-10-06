'use client';

import { useState, useEffect } from 'react';
import { ArrowLeft, FileText, Layers, Clock, Network } from 'lucide-react';
import Link from 'next/link';
import ParquetViewer from '../../components/ParquetViewer';
import LayerBadge from '../../components/LayerBadge';

interface SourceInfo {
  name: string;
  path: string;
  layers: {
    bronze: any[];
    silver: any[];
    gold: any[];
    pass1: any[];
  };
  totalFiles: number;
  totalSize: number;
}

type LayerType = 'bronze' | 'silver' | 'gold' | 'pass1';

export default function SourcePage({ params }: { params: Promise<{ id: string }> }) {
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileData, setFileData] = useState<ArrayBuffer | null>(null);
  const [loading, setLoading] = useState(false);
  const [source, setSource] = useState<SourceInfo | null>(null);
  const [sourceLoading, setSourceLoading] = useState(true);
  const [sourceId, setSourceId] = useState<string>('');
  const [activeLayer, setActiveLayer] = useState<LayerType>('bronze');

  useEffect(() => {
    const fetchSource = async () => {
      try {
        const { id } = await params;
        setSourceId(id);
        const response = await fetch(`/api/sources/${id}`);
        if (!response.ok) throw new Error('Failed to fetch source');

        const data = await response.json();
        setSource(data.source);

        // Set initial active layer to first available layer
        if (data.source.layers.bronze.length > 0) setActiveLayer('bronze');
        else if (data.source.layers.silver.length > 0) setActiveLayer('silver');
        else if (data.source.layers.gold.length > 0) setActiveLayer('gold');
        else if (data.source.layers.pass1.length > 0) setActiveLayer('pass1');
      } catch (error) {
        console.error('Error fetching source:', error);
      } finally {
        setSourceLoading(false);
      }
    };

    fetchSource();
  }, [params]);

  const handleFileSelect = async (path: string) => {
    setSelectedFile(path);
    setLoading(true);
    try {
      const response = await fetch(`/api/parquet?path=${encodeURIComponent(path)}`);
      if (!response.ok) throw new Error('Failed to load file');
      
      const data = await response.arrayBuffer();
      setFileData(data);
    } catch (error) {
      console.error('Error loading file:', error);
    } finally {
      setLoading(false);
    }
  };

  if (sourceLoading) {
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

  if (!source) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100 dark:from-gray-900 dark:to-gray-950 p-8">
        <div className="max-w-7xl mx-auto">
          <div className="text-center">
            <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Source not found</h1>
            <Link href="/dashboard" className="mt-4 inline-block text-blue-600 hover:text-blue-700">
              Back to Dashboard
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const selectedFileInfo = selectedFile ?
    [...source.layers.bronze, ...source.layers.silver, ...source.layers.gold, ...source.layers.pass1]
      .find((f: any) => f.path === selectedFile) : null;

  const availableLayers: LayerType[] = [];
  if (source.layers.bronze.length > 0) availableLayers.push('bronze');
  if (source.layers.silver.length > 0) availableLayers.push('silver');
  if (source.layers.gold.length > 0) availableLayers.push('gold');
  if (source.layers.pass1.length > 0) availableLayers.push('pass1');

  const currentLayerFiles = source.layers[activeLayer] || [];

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
              <h1 className="text-4xl font-bold text-gray-900 dark:text-white capitalize">
                {sourceId.replace(/_/g, ' ')}
              </h1>
              <p className="mt-2 text-gray-600 dark:text-gray-400">
                Explore data layers and transformations
              </p>
            </div>
            <div className="flex gap-3">
              <Link
                href="/combined"
                className="inline-flex items-center gap-2 px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 transition-colors"
              >
                <Layers className="w-4 h-4" />
                Combined Gold Tables
              </Link>
              <Link
                href={`/source/${sourceId}/mappings`}
                className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
              >
                <Network className="w-4 h-4" />
                View Mappings
              </Link>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 mb-8">
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-3 mb-2">
              <FileText className="w-5 h-5 text-blue-600" />
              <span className="text-sm font-medium text-gray-600 dark:text-gray-400">Total Files</span>
            </div>
            <p className="text-2xl font-bold text-gray-900 dark:text-white">{source.totalFiles}</p>
          </div>

          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-3 mb-2">
              <Layers className="w-5 h-5 text-green-600" />
              <span className="text-sm font-medium text-gray-600 dark:text-gray-400">Active Layers</span>
            </div>
            <div className="flex gap-2">
              {source.layers.bronze.length > 0 && <LayerBadge layer="bronze" className="scale-75" />}
              {source.layers.silver.length > 0 && <LayerBadge layer="silver" className="scale-75" />}
              {source.layers.gold.length > 0 && <LayerBadge layer="gold" className="scale-75" />}
              {source.layers.pass1.length > 0 && <LayerBadge layer="pass1" className="scale-75" />}
            </div>
          </div>

          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-3 mb-2">
              <Clock className="w-5 h-5 text-purple-600" />
              <span className="text-sm font-medium text-gray-600 dark:text-gray-400">Last Updated</span>
            </div>
            <p className="text-sm text-gray-900 dark:text-white">
              {selectedFileInfo ? new Date(selectedFileInfo.modified).toLocaleDateString() : 'Select a file'}
            </p>
          </div>

          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-3 mb-2">
              <FileText className="w-5 h-5 text-orange-600" />
              <span className="text-sm font-medium text-gray-600 dark:text-gray-400">Source Size</span>
            </div>
            <p className="text-2xl font-bold text-gray-900 dark:text-white">
              {(source.totalSize / 1024 / 1024).toFixed(2)} MB
            </p>
          </div>
        </div>

        {/* Layer Tabs */}
        <div className="mb-6">
          <div className="border-b border-gray-200 dark:border-gray-700">
            <nav className="-mb-px flex space-x-8">
              {availableLayers.map((layer) => (
                <button
                  key={layer}
                  onClick={() => {
                    setActiveLayer(layer);
                    setSelectedFile(null);
                    setFileData(null);
                  }}
                  className={`
                    whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm capitalize
                    ${activeLayer === layer
                      ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                      : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300 dark:text-gray-400 dark:hover:text-gray-300'
                    }
                  `}
                >
                  {layer} Layer ({source.layers[layer].length} files)
                </button>
              ))}
            </nav>
          </div>
        </div>

        {/* File Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-1">
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
              <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4 capitalize">
                {activeLayer} Files
              </h3>
              <div className="space-y-2">
                {currentLayerFiles.map((file: any) => (
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
                {currentLayerFiles.length === 0 && (
                  <p className="text-sm text-gray-500 dark:text-gray-400 text-center py-4">
                    No files in this layer
                  </p>
                )}
              </div>
            </div>
          </div>

          <div className="lg:col-span-2">
            {loading ? (
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
                  <FileText className="w-12 h-12 text-gray-400 mx-auto mb-4" />
                  <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-2">
                    Select a file to view
                  </h3>
                  <p className="text-sm text-gray-500 dark:text-gray-400">
                    Choose a parquet file from the list to explore its contents
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
