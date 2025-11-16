"use client"

import { useState, useEffect, useMemo } from "react"
import { Database, Plus } from "lucide-react"
import { DatasourceCard } from "./datasource-card"
import { DatasourceFilterSidebar } from "./datasource-filter-sidebar"
import { DataSource, DataSourceFilters, LICENSE_TYPES } from "../types/datasource"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { X } from "lucide-react"
import Link from "next/link"

interface DatasourceExplorerProps {
  datasources: DataSource[];
}

export function DatasourceExplorer({ datasources }: DatasourceExplorerProps) {
  const [filters, setFilters] = useState<DataSourceFilters>({})
  const [filteredDatasources, setFilteredDatasources] = useState<DataSource[]>(datasources)

  // Calculate counts for filters
  const datasourceCounts = useMemo(() => {
    const counts = {
      categories: {} as Record<string, number>,
      entityTypes: {} as Record<string, number>,
      updateCategories: {} as Record<string, number>,
      accessCategories: {} as Record<string, number>,
      healthStatuses: {} as Record<string, number>,
      licenseTypes: {} as Record<string, number>,
      evidenceLevels: {} as Record<string, number>,
      taxonScopes: {} as Record<string, number>,
      interactionTypes: {} as Record<string, number>,
      annotationTypes: {} as Record<string, number>,
      ontologyTypes: {} as Record<string, number>,
    }

    datasources.forEach(ds => {
      // Count categories and entity types from datasets
      ds.datasets.forEach(dataset => {
        counts.categories[dataset.category] = (counts.categories[dataset.category] || 0) + 1
        counts.entityTypes[dataset.entityType] = (counts.entityTypes[dataset.entityType] || 0) + 1
        counts.evidenceLevels[dataset.evidenceLevel] = (counts.evidenceLevels[dataset.evidenceLevel] || 0) + 1
        counts.taxonScopes[dataset.taxonScope] = (counts.taxonScopes[dataset.taxonScope] || 0) + 1
        
        // Count types based on category
        dataset.types.forEach(type => {
          if (dataset.category === 'interaction') {
            counts.interactionTypes[type] = (counts.interactionTypes[type] || 0) + 1
          } else if (dataset.category === 'annotation') {
            counts.annotationTypes[type] = (counts.annotationTypes[type] || 0) + 1
          } else if (dataset.category === 'ontology') {
            counts.ontologyTypes[type] = (counts.ontologyTypes[type] || 0) + 1
          }
        })
      })

      // Count resource-level attributes
      counts.updateCategories[ds.updateCategory] = (counts.updateCategories[ds.updateCategory] || 0) + 1
      counts.accessCategories[ds.accessCategory] = (counts.accessCategories[ds.accessCategory] || 0) + 1
      counts.healthStatuses[ds.health] = (counts.healthStatuses[ds.health] || 0) + 1

      // Categorize license
      const licenseType = LICENSE_TYPES.find(lt => 
        lt.regex.test(ds.license.toLowerCase())
      ) || LICENSE_TYPES[2]
      counts.licenseTypes[licenseType.value] = (counts.licenseTypes[licenseType.value] || 0) + 1
    })

    return counts
  }, [datasources])

  // Filter datasources based on current filters
  useEffect(() => {
    let filtered = [...datasources]

    // Search filter
    if (filters.search) {
      const searchLower = filters.search.toLowerCase()
      filtered = filtered.filter(ds => 
        ds.name.toLowerCase().includes(searchLower) ||
        ds.description.toLowerCase().includes(searchLower) ||
        ds.id.toLowerCase().includes(searchLower)
      )
    }

    // Category filter
    if (filters.categories && filters.categories.length > 0) {
      filtered = filtered.filter(ds =>
        ds.datasets.some(dataset => filters.categories!.includes(dataset.category))
      )
    }

    // Entity type filter
    if (filters.entityTypes && filters.entityTypes.length > 0) {
      filtered = filtered.filter(ds =>
        ds.datasets.some(dataset => filters.entityTypes!.includes(dataset.entityType))
      )
    }

    // Update category filter
    if (filters.updateCategories && filters.updateCategories.length > 0) {
      filtered = filtered.filter(ds =>
        filters.updateCategories!.includes(ds.updateCategory)
      )
    }

    // Access category filter
    if (filters.accessCategories && filters.accessCategories.length > 0) {
      filtered = filtered.filter(ds =>
        filters.accessCategories!.includes(ds.accessCategory)
      )
    }

    // Health status filter
    if (filters.healthStatuses && filters.healthStatuses.length > 0) {
      filtered = filtered.filter(ds =>
        filters.healthStatuses!.includes(ds.health)
      )
    }

    // License type filter
    if (filters.licenseTypes && filters.licenseTypes.length > 0) {
      filtered = filtered.filter(ds => {
        const licenseType = LICENSE_TYPES.find(lt => 
          lt.regex.test(ds.license.toLowerCase())
        ) || LICENSE_TYPES[2]
        return filters.licenseTypes!.includes(licenseType.value)
      })
    }

    // Evidence level filter
    if (filters.evidenceLevels && filters.evidenceLevels.length > 0) {
      filtered = filtered.filter(ds =>
        ds.datasets.some(dataset => filters.evidenceLevels!.includes(dataset.evidenceLevel))
      )
    }

    // Taxon scope filter
    if (filters.taxonScopes && filters.taxonScopes.length > 0) {
      filtered = filtered.filter(ds =>
        ds.datasets.some(dataset => filters.taxonScopes!.includes(dataset.taxonScope))
      )
    }

    // Interaction types filter
    if (filters.interactionTypes && filters.interactionTypes.length > 0) {
      filtered = filtered.filter(ds =>
        ds.datasets.some(dataset => 
          dataset.category === 'interaction' && 
          dataset.types.some(type => filters.interactionTypes!.includes(type))
        )
      )
    }

    // Annotation types filter
    if (filters.annotationTypes && filters.annotationTypes.length > 0) {
      filtered = filtered.filter(ds =>
        ds.datasets.some(dataset => 
          dataset.category === 'annotation' && 
          dataset.types.some(type => filters.annotationTypes!.includes(type))
        )
      )
    }

    // Ontology types filter
    if (filters.ontologyTypes && filters.ontologyTypes.length > 0) {
      filtered = filtered.filter(ds =>
        ds.datasets.some(dataset => 
          dataset.category === 'ontology' && 
          dataset.types.some(type => filters.ontologyTypes!.includes(type))
        )
      )
    }

    setFilteredDatasources(filtered)
  }, [filters, datasources])

  const clearAllFilters = () => {
    setFilters({})
  }

  const removeFilter = (filterType: keyof DataSourceFilters, value: string) => {
    setFilters(prev => {
      const newFilters = { ...prev }
      if (filterType === 'search') {
        delete newFilters.search
      } else if (Array.isArray(newFilters[filterType])) {
        const arr = newFilters[filterType] as string[]
        const filtered = arr.filter(v => v !== value)
        if (filtered.length === 0) {
          delete newFilters[filterType]
        } else {
          (newFilters[filterType] as string[]) = filtered
        }
      }
      return newFilters
    })
  }

  // Get active filters for display
  const activeFilters = useMemo(() => {
    const active: Array<{ type: keyof DataSourceFilters; value: string; label: string }> = []
    
    Object.entries(filters).forEach(([key, values]) => {
      if (key === 'search' && values) {
        active.push({ type: key as keyof DataSourceFilters, value: values as string, label: `Search: ${values}` })
      } else if (Array.isArray(values)) {
        values.forEach(value => {
          active.push({ type: key as keyof DataSourceFilters, value, label: value })
        })
      }
    })
    
    return active
  }, [filters])

  return (
    <div className="flex gap-6 min-h-screen">
      {/* Sidebar */}
      <aside className="w-80 flex-shrink-0 sticky top-16 h-[calc(100vh-4rem)] py-4 pl-4">
        <DatasourceFilterSidebar
          filters={filters}
          onFilterChange={setFilters}
          onClearFilters={clearAllFilters}
          datasourceCounts={datasourceCounts}
        />
      </aside>

      {/* Content Area */}
      <main className="flex-1 overflow-y-auto py-4 pr-4">
            {/* Active Filters */}
            {activeFilters.length > 0 && (
              <div className="mb-6 flex flex-wrap gap-2">
                <span className="text-sm text-muted-foreground">Active filters:</span>
                {activeFilters.map((filter, index) => (
                  <Badge
                    key={`${filter.type}-${filter.value}-${index}`}
                    variant="secondary"
                    className="flex items-center gap-1"
                  >
                    {filter.label}
                    <button
                      onClick={() => removeFilter(filter.type, filter.value)}
                      className="ml-1 hover:bg-muted rounded"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </Badge>
                ))}
              </div>
            )}

            {/* Datasource Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {/* Create New Datasource Card */}
              <Link href="/sources/datasource-new" className="block">
              <Card className="flex flex-col border-dashed border-2 bg-muted/50 hover:bg-muted/80 transition-colors cursor-pointer">
                <CardContent className="flex flex-col items-center justify-center h-full py-4 sm:py-8 text-center space-y-3">
                <div className="rounded-full bg-primary/10 p-4">
                  <Plus className="h-6 w-6 text-primary" />
                </div>
                <div className="space-y-1">
                  <h3 className="font-semibold">
                  Create New Datasource
                  </h3>
                  <p className="text-sm text-muted-foreground">
                  Add a new data source to expand our collective knowledge!
                  </p>
                </div>
                </CardContent>
              </Card>
              </Link>
              {/* Existing Datasources */}
              {filteredDatasources.length > 0 ? (
                filteredDatasources.map(datasource => (
                  <DatasourceCard key={datasource.id} datasource={datasource} />
                ))
              ) : (
                <div className="col-span-full text-center py-12">
                  <Database className="h-12 w-12 text-muted-foreground mx-auto mb-4" />
                  <h3 className="text-lg font-medium mb-2">No datasources found</h3>
                  <p className="text-muted-foreground">
                    Try adjusting your filters or search query
                  </p>
                </div>
              )}
            </div>
      </main>
    </div>
  )
}