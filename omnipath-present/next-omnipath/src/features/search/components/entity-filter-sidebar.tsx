"use client"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Filter, X } from "lucide-react"
import { cn, formatNumber, getEntityTypeEmoji } from "@/lib/utils"
import * as React from "react"
import { EntityHoverCard, CvTermHoverCard } from "@/features/search/components/result-card"

interface FilterOption {
  value: string;
  count: number;
  displayName?: string;
  icon?: string;
  id?: string | null;
}

interface EntityFilterSidebarProps {
  filters: {
    entity_types?: string[];
    sources?: string[];
    ncbi_tax_id?: string[];
  };
  filterCounts: {
    entity_type?: Record<string, number>;
    sources?: Record<string, number>;
    ncbi_tax_id?: Record<string, number>;
  };
  onFilterChange: (filters: { entity_types?: string[]; sources?: string[]; ncbi_tax_id?: string[] }) => void;
  onClearFilters: () => void;
  isMobile?: boolean;
}

// Helper component for filter sections
interface FilterSectionProps {
  title: string;
  filterKey: 'entity_types' | 'sources' | 'ncbi_tax_id';
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
    <div>
      <h4 className="text-sm font-medium mb-3">{title}</h4>
      <div className="space-y-1 max-h-64 overflow-y-auto pr-2">
        {options.map(({ value, count, displayName, icon, id }) => {
          const isSelected = selectedValues?.includes(value) || false;

          const labelContent = (
            <span className="truncate">
              {icon && <span className="mr-1.5">{icon}</span>}
              {displayName || value}
            </span>
          );

          return (
            <div key={value} className="flex items-center justify-between group py-0.5 gap-2">
              <Label
                htmlFor={`${filterKey}-${value}`}
                className={`flex items-center gap-1.5 text-xs font-normal cursor-pointer group-hover:text-primary transition-colors min-w-0 flex-1 ${isSelected ? "text-primary font-medium" : ""
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
                {id ? (
                  // Check if it's a CV term (MI: or OM:)
                  id.startsWith('MI:') || id.startsWith('OM:') ? (
                    <CvTermHoverCard termId={id}>
                      {labelContent}
                    </CvTermHoverCard>
                  ) : (
                    <EntityHoverCard entityId={id}>
                      {labelContent}
                    </EntityHoverCard>
                  )
                ) : (
                  labelContent
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
    </div>
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
  const handleToggle = (filterKey: 'entity_types' | 'sources' | 'ncbi_tax_id', value: string) => {
    const currentValues = (filters[filterKey] as string[]) || [];
    const newValues = currentValues.includes(value)
      ? currentValues.filter(v => v !== value)
      : [...currentValues, value];

    onFilterChange({
      ...filters,
      [filterKey]: newValues.length > 0 ? newValues : undefined,
    });
  };

  // Map common NCBI taxonomy IDs to organism names
  const taxonomyIdToName: Record<string, string> = {
    '9606': 'Human',
    '10090': 'Mouse',
    '10116': 'Rat',
    '7227': 'Fruit fly',
    '6239': 'C. elegans',
    '7955': 'Zebrafish',
    '559292': 'S. cerevisiae',
    '284812': 'S. pombe',
    '83333': 'E. coli',
    '224308': 'B. subtilis',
  };

  // Transform filter counts into FilterOption[] format
  const transformFilterCounts = (counts: Record<string, number>, filterKey: string): FilterOption[] => {
    return Object.entries(counts)
      .map(([value, count]) => {
        let displayName: string;
        let id: string | null = null;

        if (filterKey === 'ncbi_tax_id') {
          // For NCBI taxonomy IDs, show "Organism Name (ID)" if known, otherwise just the ID
          const organismName = taxonomyIdToName[value];
          displayName = organismName ? `${organismName} (${value})` : value;
          // We don't have hover cards for taxonomy IDs yet, and they aren't strictly CV terms or entities in the search index
          // id = value; 
        } else {
          // For entity_type and sources, extract display name from "Label:Accession" format
          // Format is "label:PREFIX:NUMBER" (e.g., "small molecule:MI:0328")
          // We want to extract just the label part
          const match = value.match(/^(.+):([A-Z]+:\d+)$/);
          if (match) {
            displayName = match[1]; // The label part (e.g., "small molecule")
            id = match[2]; // The ID part (e.g., "MI:0328")
          } else {
            // Fallback: take everything before the last colon
            const parts = value.split(':');
            if (parts.length > 1) {
              // Try to identify if the last part looks like an ID
              // Often just splitting by last colon works for ad-hoc formats too
              displayName = parts.slice(0, -1).join(':');

              // Only treat as ID if it looks like one (simple heuristic)
              // This handles cases where we might have just "Label:ID"
              const potentialId = parts[parts.length - 1];
              // Check if potentialId matches typical ID patterns (alphanumeric, maybe some special chars, but not too long/prose)
              if (potentialId.length < 20) {
                // For now, let's only be confident if we matched the specific pattern above or if it looks clearly like an ID
                // Actually, let's keep it simple: if we didn't match the strict regex, we might not have a reliable ID.
                // But wait, the previous code had `value.split(':')[0]` fallback.

                // Let's refine the regex approach.
                // For `sources`, commonly it might be `Source:ID`? 
                // Actually looking at `filter-sidebar.tsx`, they handle:
                // Agonist:MI:0001 -> label="Agonist", id="MI:0001"
                // Label:ID -> label="Label", id="ID"

                // Let's replicate that logic more closely if needed.
                // But the regex `^(.+):([A-Z]+:\d+)$` helps a lot for strict CV terms.

                // If no strict regex match:
                if (parts.length >= 2) {
                  const possiblePrefix = parts[parts.length - 2];
                  if (['MI', 'OM'].includes(possiblePrefix)) {
                    id = `${possiblePrefix}:${parts[parts.length - 1]}`;
                  } else {
                    // Maybe simple ID?
                    id = parts[parts.length - 1];
                  }
                }
              }
            } else {
              displayName = value;
            }
          }
        }

        let icon: string | undefined;
        if (filterKey === 'entity_type') {
          // Use helper function that handles normalization (spaces, underscores, case)
          icon = getEntityTypeEmoji(value);
        } else if (filterKey === 'sources') {
          // Use the same database emoji for all sources
          icon = '📚';
        }

        return {
          value: value, // Use the full facet value
          count,
          displayName,
          icon,
          id
        };
      })
      .sort((a, b) => b.count - a.count); // Sort by count in descending order
  };

  const content = (
    <div className="space-y-6">
      {/* Entity Type Filter */}
      <FilterSection
        title="Entity Types"
        filterKey="entity_types"
        options={transformFilterCounts(filterCounts.entity_type || {}, 'entity_type')}
        selectedValues={filters.entity_types || []}
        onToggle={(value) => handleToggle("entity_types", value)}
      />

      {/* Data Sources Filter */}
      <FilterSection
        title="Data Sources"
        filterKey="sources"
        options={transformFilterCounts(filterCounts.sources || {}, 'sources')}
        selectedValues={filters.sources || []}
        onToggle={(value) => handleToggle("sources", value)}
      />
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
      <CardContent className="flex-1 min-h-0 overflow-y-auto py-4">
        {content}
      </CardContent>
    </Card>
  );
}
