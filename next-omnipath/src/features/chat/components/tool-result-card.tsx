"use client"

import { Badge } from "@/components/ui/badge"
import { ChevronRight, Database, Network } from "lucide-react"
import { ToolResult } from "./dual-mode-interface"

interface ToolResultCardProps {
  toolName: string
  results: Array<Record<string, unknown>>
  query: Record<string, unknown>
  messageId: string
  onClick: (result: ToolResult) => void
  totalCount?: number
}

const getIcon = (toolName: string) => {
  switch (toolName) {
    case "searchEntities":
      return Database
    case "searchInteractions":
      return Network
    default:
      return Database
  }
}

const getToolDisplayName = (toolName: string) => {
  switch (toolName) {
    case "searchEntities":
      return "Entity Search"
    case "searchInteractions":
      return "Interaction Search"
    case "getInteractionEvidences":
      return "Interaction Details"
    default:
      return toolName
  }
}

export function ToolResultCard({ toolName, results, query, messageId, onClick, totalCount }: ToolResultCardProps) {
  const Icon = getIcon(toolName)
  const hasResults = (results && results.length > 0) || (totalCount && totalCount > 0)
  const displayCount = totalCount ?? results.length

  const handleClick = () => {
    if (hasResults) {
      onClick({
        id: `${messageId}-${toolName}`,
        toolName: toolName as ToolResult["toolName"],
        query,
        results,
        timestamp: new Date(),
        messageId,
      })
    }
  }

  if (!hasResults) return null

  return (
    <div
      className="flex items-center gap-3 p-3 rounded-lg bg-muted/50 hover:bg-muted/70 cursor-pointer transition-colors mb-2"
      onClick={handleClick}
    >
      <Icon className="w-5 h-5 text-muted-foreground" />
      <span className="font-medium text-sm">{getToolDisplayName(toolName)}</span>
      <div className="w-2 h-2 rounded-full bg-green-500" />
      <Badge variant="secondary" className="text-xs">
        {displayCount} results
      </Badge>
      <ChevronRight className="w-4 h-4 text-muted-foreground ml-auto" />
    </div>
  )
}