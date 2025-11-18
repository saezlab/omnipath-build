"use client"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Search } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

interface SearchBarProps {
  placeholder?: string
  onSearch: (query: string) => void
  isLoading?: boolean
  initialQuery?: string
  autoFocus?: boolean
  selectedSpecies?: string
  onSpeciesChange?: (species: string) => void
}

export function SearchBar({
  placeholder = "Search...",
  onSearch,
  isLoading = false,
  initialQuery = "",
  autoFocus = false,
  selectedSpecies = "9606",
  onSpeciesChange,
}: SearchBarProps) {
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

    if (query.trim() && query !== prevQueryRef.current) {
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

  const handleSearch = () => {
    if (debounceTimeout.current) {
      clearTimeout(debounceTimeout.current)
    }
    if (query.trim()) {
      onSearch(query)
      prevQueryRef.current = query
    }
  }

  return (
    <div className="w-full sticky top-0 z-10 p-6">
      <div className="max-w-7xl mx-auto relative">
        <div className="flex items-center justify-center">
          <div className="w-full max-w-2xl">
            <div className="relative group backdrop-blur-sm rounded-full transition-all focus-within:shadow-md focus-within:ring-2 focus-within:ring-primary/20">
              <Search className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground transition-colors group-focus-within:text-primary z-10" />
              <Input
                type="search"
                placeholder={placeholder}
                className="w-full pl-12 pr-[240px] h-12 text-lg rounded-full shadow-sm transition-all focus:shadow-md focus:ring-2 focus:ring-primary/20"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value)
                }}
                onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                autoFocus={autoFocus}
              />
              <div className="absolute right-28 top-1/2 -translate-y-1/2 z-10">
                <Select value={selectedSpecies} onValueChange={onSpeciesChange}>
                  <SelectTrigger className="h-8 w-auto text-xs border-0 bg-transparent shadow-none px-0 gap-1 focus:ring-0 focus:ring-offset-0 [&>span]:text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="9606">Human</SelectItem>
                    <SelectItem value="10090">Mouse</SelectItem>
                    <SelectItem value="10116">Rat</SelectItem>
                    <SelectItem value="7227">Fruit fly</SelectItem>
                    <SelectItem value="6239">C. elegans</SelectItem>
                    <SelectItem value="7955">Zebrafish</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-2 z-10">
                <Button
                  onClick={handleSearch}
                  disabled={isLoading}
                  className="h-8 px-4 rounded-full shadow-sm transition-all hover:shadow-md"
                >
                  Search
                </Button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}