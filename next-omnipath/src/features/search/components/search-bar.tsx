"use client"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Search, Filter } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover"
import { Label } from "@/components/ui/label"
import { Checkbox } from "@/components/ui/checkbox"

interface SearchBarProps {
  placeholder?: string
  onSearch: (query: string) => void
  isLoading?: boolean
  initialQuery?: string
  autoFocus?: boolean
}

export function SearchBar({
  placeholder = "Search...",
  onSearch,
  isLoading = false,
  initialQuery = "",
  autoFocus = false,
}: SearchBarProps) {
  const [query, setQuery] = useState(initialQuery)
  const debounceTimeout = useRef<NodeJS.Timeout | undefined>(undefined)
  const prevQueryRef = useRef(initialQuery)

  // State for advanced search options
  const [searchInOntologies, setSearchInOntologies] = useState(true)
  const [searchInEntities, setSearchInEntities] = useState(true)
  const [includeObsoleteTerms, setIncludeObsoleteTerms] = useState(false)
  const [exactMatchOnly, setExactMatchOnly] = useState(false)

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
              <Search className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground transition-colors group-focus-within:text-primary" />
              <Input
                type="search"
                placeholder={placeholder}
                className="w-full pl-12 pr-[200px] h-12 text-lg rounded-full shadow-sm transition-all focus:shadow-md focus:ring-2 focus:ring-primary/20"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value)
                }}
                onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                autoFocus={autoFocus}
              />
              <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-2">
                <Popover>
                  <PopoverTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8 rounded-full"
                    >
                      <Filter className="h-4 w-4" />
                    </Button>
                  </PopoverTrigger>
                <PopoverContent className="w-80">
                  <div className="grid gap-4">
                    <h4 className="font-medium leading-none">Advanced Search Options</h4>
                    <div className="space-y-4">
                      <div>
                        <h5 className="text-sm font-medium mb-2">Search Scope</h5>
                        <div className="grid gap-2">
                          <div className="flex items-center space-x-2">
                            <Checkbox
                              id="searchInOntologies"
                              checked={searchInOntologies}
                              onCheckedChange={(checked) => setSearchInOntologies(Boolean(checked))}
                            />
                            <Label htmlFor="searchInOntologies">Search in ontologies</Label>
                          </div>
                          <div className="flex items-center space-x-2">
                            <Checkbox
                              id="searchInEntities"
                              checked={searchInEntities}
                              onCheckedChange={(checked) => setSearchInEntities(Boolean(checked))}
                            />
                            <Label htmlFor="searchInEntities">Search in entities</Label>
                          </div>
                        </div>
                      </div>
                      <div>
                         <h5 className="text-sm font-medium mb-2">Options</h5>
                         <div className="grid gap-2">
                           <div className="flex items-center space-x-2">
                             <Checkbox
                               id="includeObsoleteTerms"
                               checked={includeObsoleteTerms}
                               onCheckedChange={(checked) => setIncludeObsoleteTerms(Boolean(checked))}
                             />
                             <Label htmlFor="includeObsoleteTerms">
                               Include obsolete terms
                             </Label>
                           </div>
                           <div className="flex items-center space-x-2">
                             <Checkbox
                               id="exactMatchOnly"
                               checked={exactMatchOnly}
                               onCheckedChange={(checked) => setExactMatchOnly(Boolean(checked))}
                             />
                             <Label htmlFor="exactMatchOnly">Exact match only</Label>
                           </div>
                         </div>
                      </div>
                    </div>
                  </div>
                </PopoverContent>
                </Popover>
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