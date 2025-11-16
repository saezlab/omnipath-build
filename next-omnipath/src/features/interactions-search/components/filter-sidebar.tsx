"use client"

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Slider } from "@/components/ui/slider"
import { CvTermBadge } from "@/features/cv-terms/components/cv-term-badge"
import { MeilisearchFilters, CvTermReference } from "@/types/meilisearch"
import { ArrowRight, Check, ChevronsUpDown, X, Filter } from "lucide-react"
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { cn, formatNumber, formatCount } from "@/lib/utils"
import { EntityBadge } from "@/components/entity-badge"
import { useDebounce } from "@/hooks/use-debounce"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { searchMeilisearch } from "@/features/search/api/queries"
import * as React from "react"

interface FilterOption {
  value: string;
  count: number;
  cvTerm?: CvTermReference;
}

interface FilterSidebarProps {
  filters: MeilisearchFilters;
  filterCounts: Record<string, Record<string, number>>;
  onFilterChange: (filters: MeilisearchFilters) => void;
  onClearFilters: () => void;
  selectedEntities?: Array<{ id: string; canonical_identifier: string; gene_symbol?: string }>;
  onEntitySelect?: (entity: { id: string; canonical_identifier: string; gene_symbol?: string }) => void;
  onEntityRemove?: (entityId: string) => void;
  hideEntityFilter?: boolean;
  isMobile?: boolean;
}

// Helper component for CV term filter sections
interface CvTermFilterSectionProps {
  title: string;
  filterKey: keyof MeilisearchFilters;
  options: FilterOption[];
  selectedValues: string[];
  onToggle: (id: string) => void;
}

function CvTermFilterSection({ 
  title, 
  filterKey, 
  options, 
  selectedValues, 
  onToggle
}: CvTermFilterSectionProps) {
  if (options.length === 0) return null;

  return (
    <AccordionItem value={filterKey}>
      <AccordionTrigger>{title}</AccordionTrigger>
      <AccordionContent>
        <div className="space-y-1 max-h-64 overflow-y-auto pr-2">
          {options.map(({ value, count, cvTerm }) => {
            const isSelected = selectedValues?.includes(value) || false;
            
            return (
              <div key={value} className="flex items-center justify-between group py-0.5 gap-2">
                <Label
                  htmlFor={`${filterKey}-${value}`}
                  className={`flex items-center gap-1.5 text-xs font-normal cursor-pointer group-hover:text-primary transition-colors min-w-0 flex-1 ${
                    isSelected ? "text-primary font-medium" : ""
                  }`}
                >
                  <Checkbox
                    id={`${filterKey}-${value}`}
                    checked={isSelected}
                    onCheckedChange={() => onToggle(value)}
                    className={cn(
                      "h-3.5 w-3.5 flex-shrink-0",
                      isSelected ? "border-primary" : ""
                    )}
                  />
                  {cvTerm ? (
                    <CvTermBadge
                      cvTermId={cvTerm.id}
                      cvTermName={cvTerm.name}
                      variant="outline"
                      className="text-xs py-0 px-1.5 h-5 border-0 hover:bg-transparent min-w-0"
                    />
                  ) : (
                    <span className="truncate">{value}</span>
                  )}
                </Label>
                <Badge 
                  variant={isSelected ? "default" : "outline"} 
                  className={cn(
                    "text-xs h-5 px-1.5 py-0 transition-colors flex-shrink-0",
                    "group-hover:bg-primary/10",
                    isSelected ? "bg-primary text-primary-foreground" : ""
                  )}
                >
                  {formatNumber(count)}
                </Badge>
              </div>
            );
          })}
        </div>
      </AccordionContent>
    </AccordionItem>
  );
}

export function FilterSidebar({
  filters,
  filterCounts,
  onFilterChange,
  onClearFilters,
  selectedEntities = [],
  onEntitySelect,
  onEntityRemove,
  hideEntityFilter = false,
  isMobile = false,
}: FilterSidebarProps) {
  // Calculate active filter count
  const activeFilterCount = Object.entries(filters).reduce((count, [key, value]) => {
    // Skip entity_ids when hideEntityFilter is true
    if (hideEntityFilter && key === 'entity_ids') return count;
    if (Array.isArray(value)) return count + value.length;
    if (value !== null && value !== undefined) return count + 1;
    return count;
  }, 0);

  // Handler for toggling CV term filters
  const handleCvTermToggle = (filterKey: keyof MeilisearchFilters, id: string) => {
    const currentValues = (filters[filterKey] as string[]) || [];
    const newValues = currentValues.includes(id)
      ? currentValues.filter(v => v !== id)
      : [...currentValues, id];
    
    onFilterChange({
      ...filters,
      [filterKey]: newValues.length > 0 ? newValues : undefined,
    });
  };

  // Handler for evidence count range
  const handleEvidenceCountChange = (values: number[]) => {
    const [min, max] = values;
    const evidenceCountMin = filterCounts.evidence_count?.min || 0;
    const evidenceCountMax = filterCounts.evidence_count?.max || 100;
    
    onFilterChange({
      ...filters,
      evidence_count_min: min > evidenceCountMin ? min : undefined,
      evidence_count_max: max < evidenceCountMax ? max : undefined,
    });
  };

  // Transform filter counts into FilterOption[] format
  const transformFilterCounts = (counts: Record<string, number>): FilterOption[] => {
    return Object.entries(counts)
      .map(([value, count]) => {
        // Try to parse the value as an ID:Name format
        const [id, ...nameParts] = value.split(':');
        const name = nameParts.join(':');
        
        return {
          value: value, // Use the full facet value, not just the ID
          count,
          cvTerm: name ? { id, name } : undefined
        };
      })
      .sort((a, b) => b.count - a.count); // Sort by count in descending order
  };

  const content = (
    <div className=" space-y-4">
      {/* Entity Filter */}
      {!hideEntityFilter && onEntitySelect && onEntityRemove && (
        <div className="mb-6 space-y-3">
          <Label className="text-sm font-medium">Filter by Entity</Label>
          <EntitySearchCombobox
            selectedEntities={selectedEntities}
            onEntitySelect={onEntitySelect}
            onEntityRemove={onEntityRemove}
          />
        </div>
      )}
      
      {/* Quick Filters */}
      <div className="mb-4 space-y-3">
        <Label className="text-sm font-medium">Quick Filters</Label>
        
        {/* Directionality */}
        <div className="space-y-2">
          <Label className="text-xs font-medium text-muted-foreground">DIRECTIONALITY</Label>
          <div className="flex flex-wrap gap-2">
            <Button
              variant={filters.is_directed === true ? "default" : "outline"}
              size="sm"
              onClick={() => onFilterChange({
                ...filters,
                is_directed: filters.is_directed === true ? undefined : true
              })}
            >
              <ArrowRight className="h-4 w-4 mr-1" />
              Directed { filterCounts.is_directed.true > 0 && `(${formatNumber(filterCounts.is_directed.true)})`}
            </Button>
            <Button
              variant={filters.is_directed === false ? "default" : "outline"}
              size="sm"
              onClick={() => onFilterChange({
                ...filters,
                is_directed: filters.is_directed === false ? undefined : false
              })}
            >
              Undirected { filterCounts.is_directed.false > 0 && `(${formatNumber(filterCounts.is_directed.false)})`}
            </Button>
          </div>
        </div>

      </div>

      <Accordion type="multiple" defaultValue={["interaction_types"]} className="w-full">
        {/* CV Term Filters */}
        <CvTermFilterSection
          title="Interaction Types"
          filterKey="interaction_types"
          options={transformFilterCounts(filterCounts.interaction_types)}
          selectedValues={filters.interaction_types || []}
          onToggle={(id) => handleCvTermToggle("interaction_types", id)}
        />

        <CvTermFilterSection
          title="Data Sources"
          filterKey="data_sources"
          options={transformFilterCounts(filterCounts.data_sources)}
          selectedValues={filters.data_sources || []}
          onToggle={(id) => handleCvTermToggle("data_sources", id)}
        />

        <CvTermFilterSection
          title="Detection Methods"
          filterKey="detection_methods"
          options={transformFilterCounts(filterCounts.detection_methods)}
          selectedValues={filters.detection_methods || []}
          onToggle={(id) => handleCvTermToggle("detection_methods", id)}
        />

        <CvTermFilterSection
          title="Causal Statements"
          filterKey="causal_statements"
          options={transformFilterCounts(filterCounts.causal_statements)}
          selectedValues={filters.causal_statements || []}
          onToggle={(id) => handleCvTermToggle("causal_statements", id)}
        />

        <CvTermFilterSection
          title="Causal Mechanisms"
          filterKey="causal_mechanisms"
          options={transformFilterCounts(filterCounts.causal_mechanisms)}
          selectedValues={filters.causal_mechanisms || []}
          onToggle={(id) => handleCvTermToggle("causal_mechanisms", id)}
        />

        <CvTermFilterSection
          title="Interactor Types"
          filterKey="interactor_types"
          options={transformFilterCounts(filterCounts.interactor_types)}
          selectedValues={filters.interactor_types || []}
          onToggle={(id) => handleCvTermToggle("interactor_types", id)}
        />

        {/* Evidence Count Range */}
        {filterCounts.evidence_count && (
          <AccordionItem value="evidence_count">
            <AccordionTrigger>Evidence Count</AccordionTrigger>
            <AccordionContent>
              <div className="px-4 py-2 space-y-2">
                <div className="flex items-center gap-2">
                  <span className="text-sm text-muted-foreground">Min:</span>
                  <span className="text-sm font-medium">
                    {formatNumber(filters.evidence_count_min || filterCounts.evidence_count.min)}
                  </span>
                  <span className="text-sm text-muted-foreground ml-auto">Max:</span>
                  <span className="text-sm font-medium">
                    {formatNumber(filters.evidence_count_max || filterCounts.evidence_count.max)}
                  </span>
                </div>
                <Slider
                  value={[
                    filters.evidence_count_min || filterCounts.evidence_count.min,
                    filters.evidence_count_max || filterCounts.evidence_count.max
                  ]}
                  onValueChange={handleEvidenceCountChange}
                  min={filterCounts.evidence_count.min}
                  max={filterCounts.evidence_count.max}
                  step={1}
                  className="w-full"
                />
              </div>
            </AccordionContent>
          </AccordionItem>
        )}

      </Accordion>
    </div>
  );

  if (isMobile) {
    return content;
  }

  return (
    <Card className="h-full overflow-hidden flex flex-col">
      <CardHeader className="border-b flex-shrink-0 h-[57px] flex items-center py-3">
        <div className="flex items-center justify-between w-full">
          <div className="flex items-center gap-2">
            <Filter className="h-5 w-5 text-primary" />
            <h3 className="font-semibold text-lg">Filters</h3>
          </div>
          {activeFilterCount > 0 && onClearFilters && (
            <Button 
              variant="ghost" 
              size="sm" 
              onClick={onClearFilters} 
              className="flex items-center gap-1 text-muted-foreground hover:text-foreground"
            >
              <X className="h-4 w-4" />
              Clear all ({formatNumber(activeFilterCount)})
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="flex-1 overflow-y-auto">
        {content}
      </CardContent>
    </Card>
  );
}

// Entity Search Combobox Component
interface EntitySearchComboboxProps {
  selectedEntities: Array<{ id: string; canonical_identifier: string; gene_symbol?: string }>;
  onEntitySelect: (entity: { id: string; canonical_identifier: string; gene_symbol?: string }) => void;
  onEntityRemove: (entityId: string) => void;
}

function EntitySearchCombobox({ 
  selectedEntities, 
  onEntitySelect, 
  onEntityRemove 
}: EntitySearchComboboxProps) {
  const [open, setOpen] = React.useState(false)
  const [searchQuery, setSearchQuery] = React.useState("")
  const [searchResults, setSearchResults] = React.useState<Array<{ id: string; canonical_identifier: string; gene_symbol?: string }>>([])
  const [isLoading, setIsLoading] = React.useState(false)
  
  const debouncedQuery = useDebounce(searchQuery, 300)

  // Search for entities when query changes
  React.useEffect(() => {
    const searchEntities = async () => {
      if (!debouncedQuery || debouncedQuery.length < 2) {
        setSearchResults([])
        return
      }

      setIsLoading(true)
      try {
        const response = await searchMeilisearch({
          index: "entities",
          query: debouncedQuery,
          limit: 10,
          offset: 0,
        });

        const hits = response.hits as Array<{ id: string; canonical_identifier: string; gene_symbol?: string }> || [];
        setSearchResults(hits);
      } catch (error) {
        console.error("Error searching entities:", error)
        setSearchResults([])
      } finally {
        setIsLoading(false)
      }
    }

    searchEntities()
  }, [debouncedQuery])

  return (
    <div className="space-y-3">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            aria-expanded={open}
            className="w-full justify-between"
          >
            {selectedEntities.length > 0
              ? `${formatCount(selectedEntities.length, 'entity', 'entities')} selected`
              : "Select entities..."}
            <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-[320px] p-0">
          <Command shouldFilter={false}>
            <CommandInput 
              placeholder="Search entities..." 
              value={searchQuery}
              onValueChange={setSearchQuery}
            />
            <CommandList className="max-h-72 overflow-y-auto">
              {isLoading ? (
                <CommandEmpty>Searching...</CommandEmpty>
              ) : searchQuery.length < 2 ? (
                <CommandEmpty>Type at least 2 characters to search</CommandEmpty>
              ) : searchResults.length === 0 ? (
                <CommandEmpty>No entities found.</CommandEmpty>
              ) : (
                <CommandGroup>
                  {searchResults.map((entity) => {
                    const isSelected = selectedEntities.some(e => e.id === entity.id)
                    return (
                      <CommandItem
                        key={entity.id}
                        value={entity.id}
                        onSelect={() => {
                          if (!isSelected) {
                            onEntitySelect(entity)
                            setSearchQuery("")
                            setOpen(false)
                          }
                        }}
                        disabled={isSelected}
                        className="px-3 py-2 gap-3 cursor-pointer"
                      >
                        <div className="flex-1 min-w-0">
                          <EntityBadge
                            displayName={entity.gene_symbol || ''}
                            canonicalIdentifier={entity.canonical_identifier}
                          />
                        </div>
                        <Check
                          className={cn(
                            "h-4 w-4 flex-shrink-0",
                            isSelected ? "opacity-100 text-primary" : "opacity-0"
                          )}
                        />
                      </CommandItem>
                    )
                  })}
                </CommandGroup>
              )}
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
      
      {/* Selected entities */}
      {selectedEntities.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {selectedEntities.map((entity) => (
            <Badge
              key={entity.id}
              variant="secondary"
              className="pl-2 pr-1 py-1 flex items-center gap-1"
            >
              <span className="text-xs">
                {entity.gene_symbol || entity.canonical_identifier}
              </span>
              <button
                onClick={() => onEntityRemove(entity.id)}
                className="ml-1 hover:bg-muted rounded p-0.5"
              >
                <X className="h-3 w-3" />
              </button>
            </Badge>
          ))}
        </div>
      )}
    </div>
  )
}