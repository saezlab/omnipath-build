'use client';

import { Database, FileText, Layers, Activity } from 'lucide-react';
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Separator } from "@/components/ui/separator"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { useDatabase } from "@/hooks/use-database-context"
import StatsCard from '../components/StatsCard';
import LayerBadge from '../components/LayerBadge';
import Link from 'next/link';

export default function DashboardPage() {
  const { databases, loading } = useDatabase();

  if (loading) {
    return (
      <>
        <header className="flex h-16 shrink-0 items-center gap-2 transition-[width,height] ease-linear group-has-data-[collapsible=icon]/sidebar-wrapper:h-12">
          <div className="flex items-center gap-2 px-4">
            <SidebarTrigger className="-ml-1" />
            <Separator
              orientation="vertical"
              className="mr-2 data-[orientation=vertical]:h-4"
            />
            <Breadcrumb>
              <BreadcrumbList>
                <BreadcrumbItem className="hidden md:block">
                  <BreadcrumbLink href="#">
                    OmniPath Build
                  </BreadcrumbLink>
                </BreadcrumbItem>
                <BreadcrumbSeparator className="hidden md:block" />
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
  
  const totalFiles = databases.reduce((sum, db) => sum + db.totalFiles, 0);
  const totalSize = databases.reduce((sum, db) => sum + db.totalSize, 0);
  const totalBronze = databases.reduce((sum, db) => sum + db.layers.bronze.length, 0);
  const totalSilver = databases.reduce((sum, db) => sum + db.layers.silver.length, 0);
  const totalGold = databases.reduce((sum, db) => sum + db.layers.gold.length, 0);

  return (
    <>
      <header className="flex h-16 shrink-0 items-center gap-2 transition-[width,height] ease-linear group-has-data-[collapsible=icon]/sidebar-wrapper:h-12">
        <div className="flex items-center gap-2 px-4">
          <SidebarTrigger className="-ml-1" />
          <Separator
            orientation="vertical"
            className="mr-2 data-[orientation=vertical]:h-4"
          />
          <Breadcrumb>
            <BreadcrumbList>
              <BreadcrumbItem className="hidden md:block">
                <BreadcrumbLink href="#">
                  OmniPath Build
                </BreadcrumbLink>
              </BreadcrumbItem>
              <BreadcrumbSeparator className="hidden md:block" />
              <BreadcrumbItem>
                <BreadcrumbPage>Dashboard</BreadcrumbPage>
              </BreadcrumbItem>
            </BreadcrumbList>
          </Breadcrumb>
        </div>
      </header>
      <div className="flex flex-1 flex-col gap-4 p-4 pt-0">
        <div className="mb-8">
          <h1 className="text-4xl font-bold text-gray-900 dark:text-white bg-gradient-to-r from-blue-600 to-indigo-600 bg-clip-text text-transparent">
            OmniPath Database Visualizer
          </h1>
          <p className="mt-2 text-gray-600 dark:text-gray-400">
            Explore the data transformation pipeline from Bronze to Gold
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
          <StatsCard
            title="Total Databases"
            value={databases.length}
            icon={Database}
            description="Active database pipelines"
          />
          <StatsCard
            title="Total Files"
            value={totalFiles}
            icon={FileText}
            description="Parquet files across all layers"
          />
          <StatsCard
            title="Pipeline Layers"
            value="3"
            icon={Layers}
            description="Bronze → Silver → Gold"
          />
          <StatsCard
            title="Total Size"
            value={`${(totalSize / 1024 / 1024).toFixed(1)} MB`}
            icon={Activity}
            description="Combined data volume"
          />
        </div>

        <div className="mb-8">
          <h2 className="text-2xl font-bold text-gray-900 dark:text-white mb-4">Layer Distribution</h2>
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6">
            <div className="grid grid-cols-3 gap-4">
              <div className="text-center">
                <LayerBadge layer="bronze" className="mb-2" />
                <p className="text-2xl font-bold text-gray-900 dark:text-white">{totalBronze}</p>
                <p className="text-sm text-gray-500 dark:text-gray-400">Raw data files</p>
              </div>
              <div className="text-center">
                <LayerBadge layer="silver" className="mb-2" />
                <p className="text-2xl font-bold text-gray-900 dark:text-white">{totalSilver}</p>
                <p className="text-sm text-gray-500 dark:text-gray-400">Processed files</p>
              </div>
              <div className="text-center">
                <LayerBadge layer="gold" className="mb-2" />
                <p className="text-2xl font-bold text-gray-900 dark:text-white">{totalGold}</p>
                <p className="text-sm text-gray-500 dark:text-gray-400">Analytics ready</p>
              </div>
            </div>
          </div>
        </div>

        <div>
          <h2 className="text-2xl font-bold text-gray-900 dark:text-white mb-4">Databases</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {databases.map((db) => (
              <Link
                key={db.name}
                href={`/database/${db.name}`}
                className="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6 hover:shadow-lg transition-all hover:scale-105"
              >
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <div className="p-2 bg-gradient-to-br from-blue-50 to-indigo-50 dark:from-blue-900/20 dark:to-indigo-900/20 rounded-lg">
                      <Database className="w-6 h-6 text-blue-600 dark:text-blue-400" />
                    </div>
                    <h3 className="text-lg font-semibold text-gray-900 dark:text-white capitalize">
                      {db.name.replace('_', ' ')}
                    </h3>
                  </div>
                </div>
                
                <div className="space-y-2 mb-4">
                  <div className="flex justify-between text-sm">
                    <span className="text-gray-500 dark:text-gray-400">Total Files</span>
                    <span className="font-medium text-gray-900 dark:text-white">{db.totalFiles}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-gray-500 dark:text-gray-400">Size</span>
                    <span className="font-medium text-gray-900 dark:text-white">
                      {(db.totalSize / 1024 / 1024).toFixed(2)} MB
                    </span>
                  </div>
                </div>

                <div className="flex gap-2">
                  {db.layers.bronze.length > 0 && (
                    <div className="flex items-center gap-1">
                      <LayerBadge layer="bronze" className="scale-75" />
                      <span className="text-xs text-gray-600 dark:text-gray-400">{db.layers.bronze.length}</span>
                    </div>
                  )}
                  {db.layers.silver.length > 0 && (
                    <div className="flex items-center gap-1">
                      <LayerBadge layer="silver" className="scale-75" />
                      <span className="text-xs text-gray-600 dark:text-gray-400">{db.layers.silver.length}</span>
                    </div>
                  )}
                  {db.layers.gold.length > 0 && (
                    <div className="flex items-center gap-1">
                      <LayerBadge layer="gold" className="scale-75" />
                      <span className="text-xs text-gray-600 dark:text-gray-400">{db.layers.gold.length}</span>
                    </div>
                  )}
                </div>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}
