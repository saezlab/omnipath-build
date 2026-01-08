"use client"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import { Download, Network, Table, Search } from "lucide-react"

interface DataCardProps<T extends string> {
  title: string
  children: React.ReactNode
  totalItems?: number
  viewMode: T
  onViewModeChange: (mode: T) => void
  onExport?: () => void
  className?: string
  headerActions?: React.ReactNode
  showSearch?: boolean
  searchValue?: string
  onSearchChange?: (value: string) => void
  searchPlaceholder?: string
}

export function DataCard<T extends string>({
  title,
  children,
  totalItems,
  viewMode,
  onViewModeChange,
  onExport,
  className,
  headerActions,
  showSearch,
  searchValue,
  onSearchChange,
  searchPlaceholder = "Search...",
}: DataCardProps<T>) {
  return (
    <TooltipProvider>
      <Card className={cn(
        "overflow-hidden",
        "border-2 border-muted shadow-sm hover:shadow-md",
        "bg-background",
        className
      )}>
        <CardHeader className="py-1 px-3 bg-background">
          {/* Single row layout with responsive behavior */}
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2">
            {/* Left side: Title, count, and view mode buttons */}
            <div className="flex items-center gap-2 flex-wrap">
              <div className="flex items-center gap-2">
                <h2 className="text-base font-semibold">{title}</h2>
                {totalItems !== undefined && (
                  <span className="text-sm text-muted-foreground">
                    ({totalItems.toLocaleString()})
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant={viewMode === "table" ? "default" : "ghost"}
                      size="icon"
                      onClick={() => onViewModeChange("table" as T)}
                      className="h-7 w-7"
                    >
                      <Table className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p>Table view</p>
                  </TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant={viewMode === "network" ? "default" : "ghost"}
                      size="icon"
                      onClick={() => onViewModeChange("network" as T)}
                      disabled={false}
                      className="h-7 w-7"
                    >
                      <Network className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p>Network view</p>
                  </TooltipContent>
                </Tooltip>
              </div>
            </div>
            
            {/* Right side: Search, headerActions, and Export */}
            <div className="flex items-center gap-2 w-full sm:w-auto">
              {showSearch && onSearchChange && (
                <div className="relative flex-1 sm:flex-initial">
                  <Search className="absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    placeholder={searchPlaceholder}
                    value={searchValue || ""}
                    onChange={(e) => onSearchChange(e.target.value)}
                    className="pl-8 h-7 w-full sm:w-48 md:w-64 text-sm"
                  />
                </div>
              )}
              {headerActions}
              {onExport && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button variant="outline" size="sm" onClick={onExport} className="h-7 text-sm whitespace-nowrap">
                      <Download className="h-4 w-4 mr-1.5" />
                      Export
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p>Export data</p>
                  </TooltipContent>
                </Tooltip>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-0 flex-1 overflow-hidden">{children}</CardContent>
      </Card>
    </TooltipProvider>
  )
} 