"use client"

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Filter, X } from "lucide-react"
import { cn, formatNumber } from "@/lib/utils"
import * as React from "react"

interface FilterOption {
  value: string;
  count: number;
  displayName?: string;
}

interface EntityFilterSidebarProps {
  filters: {
    entity_types?: string[];
    sources?: string[];
  };
  filterCounts: {
    entity_type?: Record<string, number>;
    sources?: Record<string, number>;
  };
  onFilterChange: (filters: { entity_types?: string[]; sources?: string[] }) => void;
  onClearFilters: () => void;
  isMobile?: boolean;
}

// Helper component for filter sections
interface FilterSectionProps {
  title: string;
  filterKey: 'entity_types' | 'sources';
  options: FilterOption[];
  selectedValues: string[];
  onToggle: (value: string) => void;
}

function FilterSection({
  title,
  filterKey,
  options,
  selectedValues,
  onToggle
}: FilterSectionProps) {
  if (options.length === 0) return null;

  return (
    <AccordionItem value={filterKey}>
      <AccordionTrigger>{title}</AccordionTrigger>
      <AccordionContent>
        <div className="space-y-1 max-h-64 overflow-y-auto pr-2">
          {options.map(({ value, count, displayName }) => {
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
                  <span className="truncate">{displayName || value}</span>
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

export function EntityFilterSidebar({
  filters,
  filterCounts,
  onFilterChange,
  onClearFilters,
  isMobile = false,
}: EntityFilterSidebarProps) {
  // Calculate active filter count
  const activeFilterCount = Object.entries(filters).reduce((count, [, value]) => {
    if (Array.isArray(value)) return count + value.length;
    if (value !== null && value !== undefined) return count + 1;
    return count;
  }, 0);

  // Handler for toggling filters
  const handleToggle = (filterKey: 'entity_types' | 'sources', value: string) => {
    const currentValues = (filters[filterKey] as string[]) || [];
    const newValues = currentValues.includes(value)
      ? currentValues.filter(v => v !== value)
      : [...currentValues, value];

    onFilterChange({
      ...filters,
      [filterKey]: newValues.length > 0 ? newValues : undefined,
    });
  };

  // Transform filter counts into FilterOption[] format
  const transformFilterCounts = (counts: Record<string, number>): FilterOption[] => {
    return Object.entries(counts)
      .map(([value, count]) => {
        // For entity_type and sources, extract display name from "Name:ID" format
        const parts = value.split(':');
        const displayName = parts.length > 1 ? parts.slice(0, -1).join(':') : value;

        return {
          value: value, // Use the full facet value
          count,
          displayName
        };
      })
      .sort((a, b) => b.count - a.count); // Sort by count in descending order
  };

  const content = (
    <div className="space-y-4">
      <Accordion type="multiple" defaultValue={["entity_types", "sources"]} className="w-full">
        {/* Entity Type Filter */}
        <FilterSection
          title="Entity Types"
          filterKey="entity_types"
          options={transformFilterCounts(filterCounts.entity_type || {})}
          selectedValues={filters.entity_types || []}
          onToggle={(value) => handleToggle("entity_types", value)}
        />

        {/* Data Sources Filter */}
        <FilterSection
          title="Data Sources"
          filterKey="sources"
          options={transformFilterCounts(filterCounts.sources || {})}
          selectedValues={filters.sources || []}
          onToggle={(value) => handleToggle("sources", value)}
        />
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
