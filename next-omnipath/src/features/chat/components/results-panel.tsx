"use client"

import { Button } from "@/components/ui/button"
import { X, Search } from "lucide-react"
import { ToolResult } from "./dual-mode-interface"
import SearchPage from "@/features/search/page"
import { EntityInteractionsSearch } from "@/features/interactions-search/components/entity-interactions-search"
import { InteractionDetails } from "@/features/interactions-search/components/interaction-details"

interface ResultsPanelProps {
  toolResult: ToolResult | null
  onClose: () => void
}

export function ResultsPanel({ toolResult, onClose }: ResultsPanelProps) {
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
        const searchType = toolResult.query.searchType as "entities" | "cv_terms" | undefined
        return (
          <SearchPage
            embedded={true}
            initialQuery={query}
            initialSearchType={searchType || "entities"}
          />
        )
      }

      case "searchInteractions": {
        const entityId = String(toolResult.query.entity_id || "")
        return (
          <EntityInteractionsSearch
            entityId={entityId}
          />
        )
      }

      case "getInteractionEvidences":
        // Find the specific interaction
        const interaction = toolResult.results[0]
        if (interaction) {
          return <InteractionDetails selectedInteraction={interaction} />
        }
        return <div className="p-4">No interaction details found</div>

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