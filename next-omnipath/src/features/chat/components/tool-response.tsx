import { ToolResultCard } from "./tool-result-card"
import { ToolResult } from "./dual-mode-interface"

// Type for tool results
interface SearchEntitiesResult {
  results: Array<Record<string, unknown>>
  totalCount: number
  searchType: "entities" | "cv_terms"
  query: string
  bestMatchId?: string | number
  componentParams?: {
    searchType: "entities" | "cv_terms"
    query: string
    limit: number
    bestMatchId?: string | number
  }
  preview?: Array<{
    id: string | number
    name: string
    type: string
    [key: string]: unknown
  }>
  stats?: {
    totalCount: number
    hasMore: boolean
  }
}

interface SearchInteractionsResult {
  results: Array<Record<string, unknown>>
  totalCount: number
  entityIds?: string[]
  filters?: Record<string, unknown>
  componentParams?: {
    entityIds?: string[]
    limit: number
  }
  preview?: Array<{
    id: string | number
    entity_a: string
    entity_b: string
    type: string
    evidence_count: number
  }>
  stats?: {
    totalCount: number
    hasMore: boolean
  }
}

interface ToolError {
  error: string
}

type ToolResultType = SearchEntitiesResult | SearchInteractionsResult | ToolError

// Update CustomToolInvocation args
interface CustomToolInvocation {
  toolName: string
  args: Record<string, unknown>
  state: string // e.g., "pending", "success", "error"
  result?: ToolResultType
}

export const ToolResponse = ({
  toolInvocation,
  onToolResultClick,
  messageId,
}: {
  toolInvocation: CustomToolInvocation
  onToolResultClick?: (result: ToolResult) => void
  messageId: string
}) => {
  const { toolName, result, state, args } = toolInvocation

  // Early exit for pending or unknown states without results yet
  if (state === 'pending' || !result) {
    return null
  }

  const isError = 'error' in result

  // Error display for any tool
  if (isError) {
    const error = result as ToolError
    return (
      <div className="flex items-center gap-2 p-2 text-sm border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950 rounded text-red-700 dark:text-red-300">
        <span className="font-medium">Error:</span>
        <span className="font-mono text-xs">{error.error}</span>
      </div>
    )
  }

  // Transform the result data to match what the results panel expects
  let transformedResults: Array<Record<string, unknown>> = []
  let query = args
  let totalCount: number | undefined

  switch (toolName) {
    case "searchEntities": {
      const searchResult = result as SearchEntitiesResult
      // Use preview data if results are empty (API returns data in preview)
      transformedResults = searchResult.results.length > 0 ? searchResult.results : (searchResult.preview || [])
      totalCount = searchResult.totalCount || searchResult.stats?.totalCount
      query = {
        query: searchResult.query,
        searchType: searchResult.searchType,
        ...args
      }
      break
    }

    case "searchInteractions": {
      const searchResult = result as SearchInteractionsResult
      // Use preview data or example interactions (results array may not exist)
      transformedResults = searchResult.results?.length > 0
        ? searchResult.results
        : (searchResult.preview || [])
      totalCount = searchResult.totalCount || searchResult.stats?.totalCount
      query = {
        entity_id: searchResult.entityIds?.[0],
        ...args
      }
      break
    }



    default:
      console.warn(`Received response for unknown tool: ${toolName}`)
      return <p className="text-sm text-muted-foreground">Unknown tool response format.</p>
  }

  return (
    <ToolResultCard
      toolName={toolName}
      results={transformedResults}
      query={query}
      messageId={messageId}
      onClick={onToolResultClick || (() => { })}
      totalCount={totalCount}
    />
  )
}