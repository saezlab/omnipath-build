"use client";

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Activity, ArrowLeft, Calendar, ChevronDown, ChevronRight, Database, Download, ExternalLink, Network, Tag, Settings2 } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { BronzeTableSample } from '../api/datasource-queries';
import { ACCESS_CATEGORIES, CATEGORIES, DataSource, ENTITY_TYPES, EVIDENCE_LEVELS, HEALTH_STATUSES, TAXON_SCOPES, UPDATE_CATEGORIES } from '../types/datasource';

interface DataSourceDetailProps {
  datasource: DataSource;
  bronzeSamples: BronzeTableSample[];
}

function getHealthBadgeVariant(health: string) {
  return health === 'success' ? 'default' : 'destructive';
}

function getUpdateCategoryIcon(category: string) {
  switch (category) {
    case 'frequent': return <Activity className="w-4 h-4" />;
    case 'infrequent': return <Calendar className="w-4 h-4" />;
    case 'discontinued': return <Activity className="w-4 h-4" />;
    case 'one_time_paper': return <Calendar className="w-4 h-4" />;
    default: return <Calendar className="w-4 h-4" />;
  }
}

function getAccessCategoryIcon(category: string) {
  switch (category) {
    case 'api': return <Network className="w-4 h-4" />;
    case 'file_download': return <Download className="w-4 h-4" />;
    case 'web_scraping': return <Database className="w-4 h-4" />;
    default: return <Download className="w-4 h-4" />;
  }
}

function getEntityTypeIcon(entityType: string) {
  const entity = ENTITY_TYPES.find(e => e.value === entityType);
  return entity?.icon || '🧬';
}

function getCategoryIcon(category: string) {
  const cat = CATEGORIES.find(c => c.value === category);
  switch (cat?.icon) {
    case 'Network': return <Network className="w-4 h-4" />;
    case 'Tag': return <Tag className="w-4 h-4" />;
    case 'Database': return <Database className="w-4 h-4" />;
    default: return <Tag className="w-4 h-4" />;
  }
}

export function DataSourceDetail({ datasource, bronzeSamples }: DataSourceDetailProps) {
  const router = useRouter();
  const [openDatasets, setOpenDatasets] = useState<Set<string>>(new Set());

  const toggleDataset = (datasetName: string) => {
    const newOpen = new Set(openDatasets);
    if (newOpen.has(datasetName)) {
      newOpen.delete(datasetName);
    } else {
      newOpen.add(datasetName);
    }
    setOpenDatasets(newOpen);
  };

  const formatCellValue = (value: unknown): string => {
    if (value === null || value === undefined) return '';
    if (typeof value === 'object') return JSON.stringify(value);
    const str = String(value);
    return str.length > 100 ? str.substring(0, 100) + '...' : str;
  };

  const healthStatus = HEALTH_STATUSES.find(h => h.value === datasource.health);
  const updateCategory = UPDATE_CATEGORIES.find(u => u.value === datasource.updateCategory);
  const accessCategory = ACCESS_CATEGORIES.find(a => a.value === datasource.accessCategory);

  return (
    <div className="container mx-auto px-4 py-8">
      {/* Header with back button */}
      <div className="flex items-center gap-4 mb-6">
        <Button 
          variant="ghost" 
          size="sm" 
          onClick={() => router.back()}
          className="flex items-center gap-2"
        >
          <ArrowLeft className="w-4 h-4" />
          Back
        </Button>
        <div>
          <h1 className="text-3xl font-bold">{datasource.name}</h1>
          <p className="text-muted-foreground">{datasource.description}</p>
        </div>
      </div>

      <div className="grid gap-6">
        {/* Main Information Card */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database className="w-5 h-5" />
              Resource Information
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              <div>
                <label className="text-sm font-medium text-muted-foreground">Health Status</label>
                <div className="mt-1">
                  <Badge variant={getHealthBadgeVariant(datasource.health)}>
                    <Activity className="w-3 h-3 mr-1" />
                    {healthStatus?.label || datasource.health}
                  </Badge>
                </div>
              </div>
              
              <div>
                <label className="text-sm font-medium text-muted-foreground">Update Frequency</label>
                <div className="mt-1">
                  <Badge variant="outline" className="flex items-center gap-1 w-fit">
                    {getUpdateCategoryIcon(datasource.updateCategory)}
                    {updateCategory?.label || datasource.updateCategory}
                  </Badge>
                </div>
              </div>
              
              <div>
                <label className="text-sm font-medium text-muted-foreground">Access Method</label>
                <div className="mt-1">
                  <Badge variant="outline" className="flex items-center gap-1 w-fit">
                    {getAccessCategoryIcon(datasource.accessCategory)}
                    {accessCategory?.label || datasource.accessCategory}
                  </Badge>
                </div>
              </div>
              
              <div>
                <label className="text-sm font-medium text-muted-foreground">License</label>
                <div className="mt-1 text-sm">{datasource.license}</div>
              </div>
              
              {datasource.website && (
                <div>
                  <label className="text-sm font-medium text-muted-foreground">Website</label>
                  <div className="mt-1">
                    <a 
                      href={datasource.website} 
                      target="_blank" 
                      rel="noopener noreferrer"
                      className="text-blue-600 hover:underline flex items-center gap-1 text-sm"
                    >
                      Visit Website
                      <ExternalLink className="w-3 h-3" />
                    </a>
                  </div>
                </div>
              )}
              
              {datasource.primaryPubmed && (
                <div>
                  <label className="text-sm font-medium text-muted-foreground">Primary Publication</label>
                  <div className="mt-1">
                    <a 
                      href={`https://pubmed.ncbi.nlm.nih.gov/${datasource.primaryPubmed}/`} 
                      target="_blank" 
                      rel="noopener noreferrer"
                      className="text-blue-600 hover:underline flex items-center gap-1 text-sm"
                    >
                      PubMed {datasource.primaryPubmed}
                      <ExternalLink className="w-3 h-3" />
                    </a>
                  </div>
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Datasets */}
        {datasource.datasets.length > 0 && (
          <Card className="w-full min-w-0">
            <CardHeader>
              <CardTitle>Datasets ({datasource.datasets.length})</CardTitle>
              <CardDescription>
                Data types and processing information for this resource
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4 w-full min-w-0">
              {datasource.datasets.map((dataset, index) => {
                const sampleData = bronzeSamples.find(s => s.datasetName === dataset.name);
                const isOpen = openDatasets.has(dataset.name);
                const entityType = ENTITY_TYPES.find(e => e.value === dataset.entityType);
                const category = CATEGORIES.find(c => c.value === dataset.category);
                const evidenceLevel = EVIDENCE_LEVELS.find(e => e.value === dataset.evidenceLevel);
                const taxonScope = TAXON_SCOPES.find(t => t.value === dataset.taxonScope);

                return (
                  <div key={index} className="border rounded-lg">
                    <Collapsible open={isOpen} onOpenChange={() => toggleDataset(dataset.name)}>
                      <CollapsibleTrigger asChild>
                        <div className="p-4 cursor-pointer hover:bg-muted/50 transition-colors">
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-3">
                              {isOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                              <div>
                                <h3 className="font-semibold">{dataset.name}</h3>
                                <div className="flex items-center gap-2 mt-1">
                                  <Badge variant="outline" className="text-xs">
                                    <span className="mr-1">{getEntityTypeIcon(dataset.entityType)}</span>
                                    {entityType?.label}
                                  </Badge>
                                  <Badge variant="outline" className="text-xs">
                                    {getCategoryIcon(dataset.category)}
                                    <span className="ml-1">{category?.label}</span>
                                  </Badge>
                                  <Badge variant="secondary" className="text-xs">
                                    {evidenceLevel?.label}
                                  </Badge>
                                  <Badge variant="secondary" className="text-xs">
                                    {taxonScope?.label}
                                  </Badge>
                                </div>
                              </div>
                            </div>
                            {sampleData && (
                              <div className="text-sm text-muted-foreground">
                                {sampleData.totalRows.toLocaleString()} rows
                              </div>
                            )}
                          </div>
                        </div>
                      </CollapsibleTrigger>
                      
                      <CollapsibleContent>
                        <div className="px-4 pb-4 border-t w-full overflow-hidden">
                          {/* Dataset metadata */}
                          <div className="py-3 space-y-2">
                            {dataset.types.length > 0 && (
                              <div>
                                <span className="text-sm font-medium text-muted-foreground">Types: </span>
                                <span className="text-sm">{dataset.types.join(", ")}</span>
                              </div>
                            )}
                          </div>

                          {/* Sample data */}
                          {sampleData && sampleData.rows.length > 0 ? (
                            <div className="space-y-3 w-full min-w-0">
                              <div className="flex items-center justify-between">
                                <h4 className="font-medium">Sample Data (first 100 rows)</h4>
                                <Badge variant="outline" className="text-xs">
                                  {sampleData.tableName}
                                </Badge>
                              </div>
                              
                              <div className="border rounded-md" style={{ contain: 'layout' }}>
                                <div 
                                  className="overflow-x-auto max-h-96" 
                                  style={{ 
                                    width: '100%',
                                    maxWidth: '100%',
                                    display: 'block'
                                  }}
                                >
                                  <table className="divide-y divide-border text-xs">
                                    <thead className="bg-muted/50 sticky top-0 z-10">
                                      <tr>
                                        {sampleData.columns.map((col) => (
                                          <th 
                                            key={col} 
                                            className="px-3 py-2 text-left font-medium whitespace-nowrap"
                                          >
                                            {col}
                                          </th>
                                        ))}
                                      </tr>
                                    </thead>
                                    <tbody className="bg-background divide-y divide-border">
                                      {sampleData.rows.slice(0, 10).map((row, rowIndex) => (
                                        <tr key={rowIndex} className="hover:bg-muted/50">
                                          {sampleData.columns.map((col) => (
                                            <td 
                                              key={col} 
                                              className="px-3 py-2 whitespace-nowrap"
                                            >
                                              <div className="max-w-[300px] truncate" title={String(row[col] || '')}>
                                                {formatCellValue(row[col])}
                                              </div>
                                            </td>
                                          ))}
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                </div>
                                {sampleData.rows.length > 10 && (
                                  <div className="p-2 border-t bg-muted/50 text-xs text-muted-foreground text-center">
                                    Showing 10 of {sampleData.rows.length} sample rows
                                  </div>
                                )}
                              </div>
                            </div>
                          ) : (
                            <div className="text-sm text-muted-foreground py-4 text-center">
                              No sample data available for this dataset
                            </div>
                          )}
                          
                          {/* Configure Mapping Button */}
                          <div className="border-t pt-4 mt-4">
                            <Button
                              onClick={() => router.push(`/sources/${datasource.id}/datasets/${dataset.name}/mapping`)}
                              variant="outline"
                              className="w-full flex items-center justify-center gap-2"
                            >
                              <Settings2 className="w-4 h-4" />
                              Configure Column Mapping
                            </Button>
                          </div>
                        </div>
                      </CollapsibleContent>
                    </Collapsible>
                  </div>
                );
              })}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}