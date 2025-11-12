'use client';

import { useState } from 'react';
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
} from "@/components/ui/breadcrumb";
import SourceConfigViewer from '../components/SourceConfigViewer';
import GoldTableViewer from '../components/GoldTableViewer';

type ConfigTab = 'bronze-silver' | 'silver-gold';

export default function ConfigPage() {
  const [activeTab, setActiveTab] = useState<ConfigTab>('bronze-silver');

  return (
    <>
      <header className="flex h-16 shrink-0 items-center gap-2">
        <div className="flex items-center gap-2 px-4 w-full">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-2 h-4" />
          <Breadcrumb>
            <BreadcrumbList>
              <BreadcrumbItem>
                <BreadcrumbPage>Configuration</BreadcrumbPage>
              </BreadcrumbItem>
            </BreadcrumbList>
          </Breadcrumb>
        </div>
      </header>

      <div className="flex flex-1 flex-col gap-4 p-4 pt-0">
        <div className="mb-4">
          <h1 className="text-3xl font-bold text-gray-900 dark:text-white">
            Pipeline Configuration
          </h1>
          <p className="mt-1 text-gray-600 dark:text-gray-400">
            View transformation rules and mappings across the data pipeline
          </p>
        </div>

        {/* Tab Navigation */}
        <div className="border-b border-gray-200 dark:border-gray-700">
          <nav className="-mb-px flex space-x-8">
            <button
              onClick={() => setActiveTab('bronze-silver')}
              className={`
                whitespace-nowrap py-3 px-1 border-b-2 font-medium text-sm
                ${activeTab === 'bronze-silver'
                  ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300 dark:text-gray-400 dark:hover:text-gray-300'
                }
              `}
            >
              Bronze → Silver
              <span className="ml-2 text-xs text-gray-500 dark:text-gray-400">(Source-Specific)</span>
            </button>
            <button
              onClick={() => setActiveTab('silver-gold')}
              className={`
                whitespace-nowrap py-3 px-1 border-b-2 font-medium text-sm
                ${activeTab === 'silver-gold'
                  ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300 dark:text-gray-400 dark:hover:text-gray-300'
                }
              `}
            >
              Silver → Gold
              <span className="ml-2 text-xs text-gray-500 dark:text-gray-400">(Global)</span>
            </button>
          </nav>
        </div>

        {/* Tab Content */}
        <div className="flex-1 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
          {activeTab === 'bronze-silver' && (
            <div>
              <div className="mb-4">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-white">
                  Bronze → Silver Transformations
                </h2>
                <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                  Source-specific field mappings and transformation rules from raw data to standardized silver tables
                </p>
              </div>
              <SourceConfigViewer />
            </div>
          )}

          {activeTab === 'silver-gold' && (
            <div>
              <div className="mb-4">
                <h2 className="text-xl font-semibold text-gray-900 dark:text-white">
                  Silver → Gold Transformations
                </h2>
                <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                  Global SQL queries that aggregate silver tables into final gold tables with relationships
                </p>
              </div>
              <GoldTableViewer />
            </div>
          )}
        </div>
      </div>
    </>
  );
}
