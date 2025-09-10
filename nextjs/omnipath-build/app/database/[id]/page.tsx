'use client';

import { useState, useEffect } from 'react';
import { ArrowLeft, FileText, Layers, Clock } from 'lucide-react';
import Link from 'next/link';
import DatabaseTree from '../../components/DatabaseTree';
import ParquetViewer from '../../components/ParquetViewer';
import LayerBadge from '../../components/LayerBadge';

interface DatabaseInfo {
  name: string;
  path: string;
  layers: {
    bronze: any[];
    silver: any[];
    gold: any[];
  };
  totalFiles: number;
  totalSize: number;
}

export default function DatabasePage({ params }: { params: Promise<{ id: string }> }) {
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileData, setFileData] = useState<ArrayBuffer | null>(null);
  const [loading, setLoading] = useState(false);
  const [database, setDatabase] = useState<DatabaseInfo | null>(null);
  const [treeData, setTreeData] = useState<any[]>([]);
  const [dbLoading, setDbLoading] = useState(true);
  const [dbId, setDbId] = useState<string>('');

  useEffect(() => {
    const fetchDatabase = async () => {
      try {
        const { id } = await params;
        setDbId(id);
        const response = await fetch(`/api/databases/${id}`);
        if (!response.ok) throw new Error('Failed to fetch database');
        
        const data = await response.json();
        setDatabase(data.database);
        setTreeData(data.tree);
      } catch (error) {
        console.error('Error fetching database:', error);
      } finally {
        setDbLoading(false);
      }
    };

    fetchDatabase();
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

  if (dbLoading) {
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
  
  if (!database) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100 dark:from-gray-900 dark:to-gray-950 p-8">
        <div className="max-w-7xl mx-auto">
          <div className="text-center">
            <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Database not found</h1>
            <Link href="/dashboard" className="mt-4 inline-block text-blue-600 hover:text-blue-700">
              Back to Dashboard
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const selectedFileInfo = selectedFile ? 
    [...database.layers.bronze, ...database.layers.silver, ...database.layers.gold]
      .find((f: any) => f.path === selectedFile) : null;

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
          
          <h1 className="text-4xl font-bold text-gray-900 dark:text-white capitalize">
            {dbId.replace('_', ' ')} Database
          </h1>
          <p className="mt-2 text-gray-600 dark:text-gray-400">
            Explore data layers and transformations
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 mb-8">
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-3 mb-2">
              <FileText className="w-5 h-5 text-blue-600" />
              <span className="text-sm font-medium text-gray-600 dark:text-gray-400">Total Files</span>
            </div>
            <p className="text-2xl font-bold text-gray-900 dark:text-white">{database.totalFiles}</p>
          </div>
          
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-3 mb-2">
              <Layers className="w-5 h-5 text-green-600" />
              <span className="text-sm font-medium text-gray-600 dark:text-gray-400">Active Layers</span>
            </div>
            <div className="flex gap-2">
              {database.layers.bronze.length > 0 && <LayerBadge layer="bronze" className="scale-75" />}
              {database.layers.silver.length > 0 && <LayerBadge layer="silver" className="scale-75" />}
              {database.layers.gold.length > 0 && <LayerBadge layer="gold" className="scale-75" />}
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
              <span className="text-sm font-medium text-gray-600 dark:text-gray-400">Database Size</span>
            </div>
            <p className="text-2xl font-bold text-gray-900 dark:text-white">
              {(database.totalSize / 1024 / 1024).toFixed(2)} MB
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-1">
            <DatabaseTree data={treeData} onFileSelect={handleFileSelect} />
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
                    Choose a parquet file from the tree to explore its contents
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
