"use client"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Search, X } from "lucide-react"
import { useEffect, useRef, useState } from "react"

interface DatasourceSearchBarProps {
  placeholder?: string
  onSearch: (query: string) => void
  isLoading?: boolean
  initialQuery?: string
}

export function DatasourceSearchBar({
  placeholder = "Search datasources by name, description, or ID...",
  onSearch,
  initialQuery = "",
}: DatasourceSearchBarProps) {
  const [query, setQuery] = useState(initialQuery)
  const debounceTimeout = useRef<NodeJS.Timeout | undefined>(undefined)
  const prevQueryRef = useRef(initialQuery)

  useEffect(() => {
    if (initialQuery !== prevQueryRef.current) {
      setQuery(initialQuery)
      prevQueryRef.current = initialQuery
    }
  }, [initialQuery])

  useEffect(() => {
    if (debounceTimeout.current) {
      clearTimeout(debounceTimeout.current)
    }

    if (query !== prevQueryRef.current) {
      debounceTimeout.current = setTimeout(() => {
        onSearch(query)
        prevQueryRef.current = query
      }, 300)
    }

    return () => {
      if (debounceTimeout.current) {
        clearTimeout(debounceTimeout.current)
      }
    }
  }, [query, onSearch])

  const handleClear = () => {
    setQuery("")
    onSearch("")
    prevQueryRef.current = ""
  }

  return (
    <div className="relative group">
      <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground transition-colors group-focus-within:text-primary" />
      <Input
        type="search"
        placeholder={placeholder}
        className="w-full pl-10 pr-10 h-10 transition-all focus:ring-2 focus:ring-primary/20"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            handleClear()
          }
        }}
      />
      {query && (
        <Button
          variant="ghost"
          size="icon"
          className="absolute right-1 top-1/2 h-8 w-8 -translate-y-1/2"
          onClick={handleClear}
        >
          <X className="h-4 w-4" />
        </Button>
      )}
    </div>
  )
}