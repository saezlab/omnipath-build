"use client"

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { MeilisearchFilters } from "@/types/meilisearch"
import { ArrowRight, Plus, Minus, X, Filter } from "lucide-react"
import { cn, formatNumber, getEntityTypeEmoji } from "@/lib/utils"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { EntityHoverCard } from "@/features/search/components/result-card"

interface FilterOption {
  value: string;
  count: number;
  label?: string;
  icon?: string;
}

interface FilterSidebarProps {
  filters: MeilisearchFilters;
  filterCounts: Record<string, Record<string, number>>;
  onFilterChange: (filters: MeilisearchFilters) => void;
  onClearFilters: () => void;
  isMobile?: boolean;
}

// Helper component for array filter sections
interface ArrayFilterSectionProps {
  title: string;
  filterKey: keyof MeilisearchFilters;
  options: FilterOption[];
  selectedValues: string[];
  onToggle: (value: string) => void;
  showHoverCard?: boolean;
  showIcon?: boolean;
}

function ArrayFilterSection({
  title,
  filterKey,
  options,
  selectedValues,
  onToggle,
  showHoverCard = false,
  showIcon = false
}: ArrayFilterSectionProps) {
  if (options.length === 0) return null;

  return (
    <AccordionItem value={filterKey}>
      <AccordionTrigger>{title}</AccordionTrigger>
      <AccordionContent>
        <div className="space-y-1 max-h-64 overflow-y-auto pr-2">
          {options.map(({ value, count, label, icon }) => {
            const isSelected = selectedValues?.includes(value) || false;
            // Parse label and ID from "Label:ID" format if present
            const parts = value.includes(':') ? value.split(':') : [value];
            const displayLabel = label || parts[0];
            const entityId = parts.length > 1 ? parts[1] : null;

            const labelContent = (
              <span className="truncate">
                {showIcon && icon && <span className="mr-1.5">{icon}</span>}
                {displayLabel}
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
                  {showHoverCard && entityId ? (
                    <EntityHoverCard entityId={entityId}>
                      {labelContent}
                    </EntityHoverCard>
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
      </AccordionContent>
    </AccordionItem>
  );
}

export function FilterSidebar({
  filters,
  filterCounts,
  onFilterChange,
  onClearFilters,
  isMobile = false,
}: FilterSidebarProps) {
  // Calculate active filter count
  const activeFilterCount = Object.entries(filters).reduce((count, [, value]) => {
    if (Array.isArray(value)) return count + value.length;
    if (value !== null && value !== undefined) return count + 1;
    return count;
  }, 0);

  // Handler for toggling array filters
  const handleArrayToggle = (filterKey: keyof MeilisearchFilters, value: string) => {
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
  const transformFilterCounts = (
    counts: Record<string, number> | undefined,
    filterKey?: string
  ): FilterOption[] => {
    if (!counts) return [];
    return Object.entries(counts)
      .map(([value, count]) => {
        const label = value.includes(':') ? value.split(':')[0] : value;
        // Get emoji icon for member_types filter
        const icon = filterKey === 'member_types' ? getEntityTypeEmoji(value) : undefined;
        return {
          value,
          count,
          label,
          icon
        };
      })
      .sort((a, b) => b.count - a.count);
  };

  // Handler for clearing entity filter
  const handleClearEntityFilter = () => {
    // Remove entity-related filters, keep the rest
    const { member_a_id, member_b_id, entity_ids, ...rest } = filters;
    void member_a_id; void member_b_id; void entity_ids; // Explicitly ignore
    onFilterChange(rest);
  };

  // Check if we have any entity filters
  const hasEntityFilter = filters.member_a_id || filters.entity_ids?.length;
  const entityFilterDisplay = filters.entity_ids?.length
    ? `${filters.entity_ids.length} ${filters.entity_ids.length === 1 ? 'entity' : 'entities'}`
    : filters.member_a_id
      ? `Entity ID: ${filters.member_a_id}`
      : null;

  const content = (
    <div className="space-y-4">
      {/* Entity Filter Badge */}
      {hasEntityFilter && (
        <div className="space-y-2">
          <Label className="text-xs font-medium text-muted-foreground">ENTITY FILTER</Label>
          <div className="flex items-center gap-2">
            <Badge variant="secondary" className="flex items-center gap-1 py-1 px-2">
              <span>{entityFilterDisplay}</span>
              <Button
                variant="ghost"
                size="sm"
                className="h-4 w-4 p-0 hover:bg-transparent"
                onClick={handleClearEntityFilter}
              >
                <X className="h-3 w-3" />
              </Button>
            </Badge>
          </div>
        </div>
      )}

      {/* Quick Filters */}
      <div className="mb-4 space-y-3">
        {/* Directionality */}
        <div className="space-y-2">
          <Label className="text-xs font-medium text-muted-foreground">DIRECTIONALITY</Label>
          <div className="flex flex-wrap gap-2">
            <Button
              variant={filters.has_direction === true ? "default" : "outline"}
              size="sm"
              onClick={() => onFilterChange({
                ...filters,
                has_direction: filters.has_direction === true ? undefined : true
              })}
            >
              <ArrowRight className="h-4 w-4 mr-1" />
              Directed {filterCounts.has_direction?.true > 0 && `(${formatNumber(filterCounts.has_direction.true)})`}
            </Button>
            <Button
              variant={filters.has_direction === false ? "default" : "outline"}
              size="sm"
              onClick={() => onFilterChange({
                ...filters,
                has_direction: filters.has_direction === false ? undefined : false
              })}
            >
              <Minus className="h-4 w-4 mr-1" />
              Undirected {filterCounts.has_direction?.false > 0 && `(${formatNumber(filterCounts.has_direction.false)})`}
            </Button>
          </div>
        </div>

        {/* Sign Filters */}
        <div className="space-y-2">
          <Label className="text-xs font-medium text-muted-foreground">EFFECT</Label>
          <div className="flex flex-wrap gap-2">
            <Button
              variant={filters.has_positive_sign === true ? "default" : "outline"}
              size="sm"
              onClick={() => onFilterChange({
                ...filters,
                has_positive_sign: filters.has_positive_sign === true ? undefined : true
              })}
              className={filters.has_positive_sign === true ? "bg-green-600 hover:bg-green-700" : ""}
            >
              <Plus className="h-4 w-4 mr-1" />
              Activation {filterCounts.has_positive_sign?.true > 0 && `(${formatNumber(filterCounts.has_positive_sign.true)})`}
            </Button>
            <Button
              variant={filters.has_negative_sign === true ? "default" : "outline"}
              size="sm"
              onClick={() => onFilterChange({
                ...filters,
                has_negative_sign: filters.has_negative_sign === true ? undefined : true
              })}
              className={filters.has_negative_sign === true ? "bg-red-600 hover:bg-red-700" : ""}
            >
              <Minus className="h-4 w-4 mr-1" />
              Inhibition {filterCounts.has_negative_sign?.true > 0 && `(${formatNumber(filterCounts.has_negative_sign.true)})`}
            </Button>
          </div>
        </div>
      </div>

      <Accordion type="multiple" defaultValue={["member_types"]} className="w-full">
        {/* Member Types Filter */}
        <ArrayFilterSection
          title="Member Types"
          filterKey="member_types"
          options={transformFilterCounts(filterCounts.member_types, 'member_types')}
          selectedValues={filters.member_types || []}
          onToggle={(value) => handleArrayToggle("member_types", value)}
          showHoverCard={true}
          showIcon={true}
        />

        {/* Interaction Annotation Terms Filter */}
        <ArrayFilterSection
          title="Annotation Terms"
          filterKey="interaction_annotation_terms"
          options={transformFilterCounts(filterCounts.interaction_annotation_terms)}
          selectedValues={filters.interaction_annotation_terms || []}
          onToggle={(value) => handleArrayToggle("interaction_annotation_terms", value)}
          showHoverCard={true}
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
      <CardContent className="flex-1 min-h-0 overflow-y-auto py-4">
        {content}
      </CardContent>
    </Card>
  );
}
