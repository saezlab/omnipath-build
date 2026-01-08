"use client"

import { Button } from "@/components/ui/button"
import { X, Search } from "lucide-react"
import { ToolResult } from "./dual-mode-interface"
import SearchPage from "@/features/search/page"
import { InteractionsExploreTab } from "@/features/explore/components/interactions-explore-tab"
import { useState, useCallback, useEffect } from "react"
import { MeilisearchFilters } from "@/types/meilisearch"

interface ResultsPanelProps {
  toolResult: ToolResult | null
  onClose: () => void
}

export function ResultsPanel({ toolResult, onClose }: ResultsPanelProps) {
  // State for interactions tab
  const [interactionsFilters, setInteractionsFilters] = useState<MeilisearchFilters>({})
  const [, setInteractionsFilterCounts] = useState<Record<string, Record<string, number>>>({})

  // Reset filters when toolResult changes
  useEffect(() => {
    if (toolResult?.toolName === "searchInteractions") {
      const query = toolResult.query
      let ids: number[] = []

      if (Array.isArray(query.entityIds)) {
        ids = query.entityIds.map(id => Number(id)).filter(id => !isNaN(id))
      } else if (query.entity_id) {
        const id = Number(query.entity_id)
        if (!isNaN(id)) ids.push(id)
      }

      if (ids.length > 0) {
        setInteractionsFilters({ entity_ids: ids })
      } else {
        setInteractionsFilters({})
      }
    }
  }, [toolResult])

  const handleInteractionsFilterChange = useCallback((newFilters: MeilisearchFilters) => {
    setInteractionsFilters(newFilters)
  }, [])

  const handleInteractionsFilterCountsUpdate = useCallback((counts: Record<string, Record<string, number>>) => {
    setInteractionsFilterCounts(counts)
  }, [])

  if (!toolResult) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground">
        <div className="text-center">
          <Search className="w-12 h-12 mx-auto mb-4 opacity-50" />
          <p>Click on a search result to view details</p>
        </div>
      </div>
    )
  }

  const renderResultsContent = () => {
    switch (toolResult.toolName) {
      case "searchEntities": {
        const query = String(toolResult.query.query || "")
        // Map "entities" to "search_entities" to match SearchPage props
        const rawSearchType = toolResult.query.searchType as string
        const searchType = rawSearchType === "cv_terms" ? "cv_terms" : "search_entities"

        return (
          <SearchPage
            embedded={true}
            initialQuery={query}
            initialSearchType={searchType}
          />
        )
      }

      case "searchInteractions": {
        return (
          <div className="h-full p-4">
            <InteractionsExploreTab
              filters={interactionsFilters}
              onFilterChange={handleInteractionsFilterChange}
              onFilterCountsUpdate={handleInteractionsFilterCountsUpdate}
            />
          </div>
        )
      }



      default:
        return (
          <div className="p-4">
            <pre className="text-sm">{JSON.stringify(toolResult.results, null, 2)}</pre>
          </div>
        )
    }
  }

  return (
    <div className="h-full flex flex-col bg-muted/20 relative">
      {/* Floating close button on the left */}
      <Button
        variant="secondary"
        size="icon"
        className="absolute top-0 left-2 z-10 shadow-md"
        onClick={onClose}
      >
        <X className="w-4 h-4" />
      </Button>

      <div className="h-full overflow-auto">
        {renderResultsContent()}
      </div>
    </div>
  )
}