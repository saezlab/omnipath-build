'use client';

import { useState, useEffect } from 'react';
import { ArrowLeft, Network, Layers, ChevronDown } from 'lucide-react';
import Link from 'next/link';
import MappingVisualization from '@/components/MappingVisualization';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";

interface MappingData {
  resource: string;
  module: string;
  function: string;
  targetTable: string;
  fieldMappings: Array<{
    source: string;
    target: string;
    transform?: string;
    description?: string;
  }>;
  description: string;
}

interface MappingsResponse {
  database: string;
  mappings: MappingData[];
  bronzeTables: Array<{ resource: string; table: string }>;
  silverTables: string[];
  silverTableDefinitions?: Record<string, Record<string, string>>;
}

export default function MappingsPage({ params }: { params: Promise<{ id: string }> }) {
  const [mappingsData, setMappingsData] = useState<MappingsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dbId, setDbId] = useState<string>('');
  const [selectedSource, setSelectedSource] = useState<string>('all');

  useEffect(() => {
    const fetchMappings = async () => {
      try {
        const { id } = await params;
        setDbId(id);
        const response = await fetch(`/api/databases/${id}/mappings`);
        
        if (!response.ok) {
          throw new Error('Failed to fetch mapping data');
        }
        
        const data = await response.json();
        setMappingsData(data);
        // Set the first source as default if available
        if (data.mappings && data.mappings.length > 0) {
          setSelectedSource(data.mappings[0].resource);
        }
      } catch (err) {
        console.error('Error fetching mappings:', err);
        setError(err instanceof Error ? err.message : 'An error occurred');
      } finally {
        setLoading(false);
      }
    };

    fetchMappings();
  }, [params]);

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

  if (error) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100 dark:from-gray-900 dark:to-gray-950 p-8">
        <div className="max-w-7xl mx-auto">
          <div className="text-center">
            <h1 className="text-2xl font-bold text-gray-900 dark:text-white mb-4">Error loading mappings</h1>
            <p className="text-gray-600 dark:text-gray-400 mb-4">{error}</p>
            <Link href={`/database/${dbId}`} className="text-blue-600 hover:text-blue-700">
              Back to Database
            </Link>
          </div>
        </div>
      </div>
    );
  }

  if (!mappingsData) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100 dark:from-gray-900 dark:to-gray-950 p-8">
        <div className="max-w-7xl mx-auto">
          <div className="text-center">
            <h1 className="text-2xl font-bold text-gray-900 dark:text-white">No mapping data found</h1>
            <Link href={`/database/${dbId}`} className="mt-4 inline-block text-blue-600 hover:text-blue-700">
              Back to Database
            </Link>
          </div>
        </div>
      </div>
    );
  }

  // Get unique sources from mappings
  const uniqueSources = mappingsData ? 
    [...new Set(mappingsData.mappings.map(m => m.resource))] : [];
  
  // Filter mappings based on selected source
  const filteredMappings = selectedSource === 'all' 
    ? mappingsData?.mappings || []
    : mappingsData?.mappings.filter(m => m.resource === selectedSource) || [];

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100 dark:from-gray-900 dark:to-gray-950">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="mb-8">
          <div className="flex items-center gap-4 mb-4">
            <Link 
              href={`/database/${dbId}`}
              className="inline-flex items-center gap-2 text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white"
            >
              <ArrowLeft className="w-4 h-4" />
              Back to Database
            </Link>
          </div>
          
          <div className="flex items-center gap-3 mb-2">
            <Network className="w-8 h-8 text-blue-600" />
            <h1 className="text-4xl font-bold text-gray-900 dark:text-white capitalize">
              {dbId.replace('_', ' ')} Data Mappings
            </h1>
          </div>
          <p className="mt-2 text-gray-600 dark:text-gray-400">
            Visualizing data transformation from Bronze to Silver layer
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 mb-8">
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-3 mb-2">
              <Layers className="w-5 h-5 text-amber-600" />
              <span className="text-sm font-medium text-gray-600 dark:text-gray-400">Bronze Tables</span>
            </div>
            <p className="text-2xl font-bold text-gray-900 dark:text-white">
              {mappingsData.bronzeTables.length}
            </p>
          </div>
          
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-3 mb-2">
              <Layers className="w-5 h-5 text-gray-600" />
              <span className="text-sm font-medium text-gray-600 dark:text-gray-400">Silver Tables</span>
            </div>
            <p className="text-2xl font-bold text-gray-900 dark:text-white">
              {mappingsData.silverTables.length}
            </p>
          </div>
          
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-3 mb-2">
              <Network className="w-5 h-5 text-blue-600" />
              <span className="text-sm font-medium text-gray-600 dark:text-gray-400">Mappings</span>
            </div>
            <p className="text-2xl font-bold text-gray-900 dark:text-white">
              {mappingsData.mappings.length}
            </p>
          </div>
          
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-3 mb-2">
              <Network className="w-5 h-5 text-green-600" />
              <span className="text-sm font-medium text-gray-600 dark:text-gray-400">Field Connections</span>
            </div>
            <p className="text-2xl font-bold text-gray-900 dark:text-white">
              {filteredMappings.reduce((acc, m) => acc + m.fieldMappings.length, 0)}
            </p>
          </div>
        </div>

        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6" style={{ height: '700px' }}>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
              Bronze to Silver Data Flow
            </h2>
            {uniqueSources.length > 0 && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" className="w-48">
                    <span className="truncate">
                      {selectedSource === 'all' ? 'All Sources' : selectedSource}
                    </span>
                    <ChevronDown className="ml-2 h-4 w-4 shrink-0" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-48">
                  <DropdownMenuItem onClick={() => setSelectedSource('all')}>
                    All Sources
                  </DropdownMenuItem>
                  {uniqueSources.map((source) => (
                    <DropdownMenuItem 
                      key={source} 
                      onClick={() => setSelectedSource(source)}
                    >
                      {source}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>
          {filteredMappings.length > 0 ? (
            <div style={{ height: 'calc(100% - 60px)' }}>
              <MappingVisualization 
                mappings={filteredMappings}
                bronzeTables={mappingsData.bronzeTables}
                silverTables={mappingsData.silverTables}
                silverTableDefinitions={mappingsData.silverTableDefinitions}
              />
            </div>
          ) : (
            <div className="flex items-center justify-center h-full">
              <div className="text-center">
                <Network className="w-12 h-12 text-gray-400 mx-auto mb-4" />
                <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-2">
                  No mappings configured
                </h3>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  This database doesn't have any bronze to silver mappings configured yet
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
