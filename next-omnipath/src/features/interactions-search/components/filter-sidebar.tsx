"use client"

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { MeilisearchFilters } from "@/types/meilisearch"
import { ArrowRight, Plus, Minus, X, Filter } from "lucide-react"
import { cn, formatNumber } from "@/lib/utils"
import { Card, CardContent, CardHeader } from "@/components/ui/card"

interface FilterOption {
  value: string;
  count: number;
  label?: string;
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
}

function ArrayFilterSection({
  title,
  filterKey,
  options,
  selectedValues,
  onToggle
}: ArrayFilterSectionProps) {
  if (options.length === 0) return null;

  return (
    <AccordionItem value={filterKey}>
      <AccordionTrigger>{title}</AccordionTrigger>
      <AccordionContent>
        <div className="space-y-1 max-h-64 overflow-y-auto pr-2">
          {options.map(({ value, count, label }) => {
            const isSelected = selectedValues?.includes(value) || false;
            // Parse label from "Label:ID" format if present
            const displayLabel = label || (value.includes(':') ? value.split(':')[0] : value);

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
                  <span className="truncate">{displayLabel}</span>
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
  const transformFilterCounts = (counts: Record<string, number> | undefined): FilterOption[] => {
    if (!counts) return [];
    return Object.entries(counts)
      .map(([value, count]) => ({
        value,
        count,
        label: value.includes(':') ? value.split(':')[0] : value
      }))
      .sort((a, b) => b.count - a.count);
  };

  // Handler for clearing entity filter
  const handleClearEntityFilter = () => {
    const { member_a_id, member_b_id, ...rest } = filters;
    onFilterChange(rest);
  };

  const content = (
    <div className="space-y-4">
      {/* Entity Filter Badge */}
      {filters.member_a_id && (
        <div className="space-y-2">
          <Label className="text-xs font-medium text-muted-foreground">ENTITY FILTER</Label>
          <div className="flex items-center gap-2">
            <Badge variant="secondary" className="flex items-center gap-1 py-1 px-2">
              <span>Entity ID: {filters.member_a_id}</span>
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
          options={transformFilterCounts(filterCounts.member_types)}
          selectedValues={filters.member_types || []}
          onToggle={(value) => handleArrayToggle("member_types", value)}
        />

        {/* Interaction Annotation Terms Filter */}
        <ArrayFilterSection
          title="Annotation Terms"
          filterKey="interaction_annotation_terms"
          options={transformFilterCounts(filterCounts.interaction_annotation_terms)}
          selectedValues={filters.interaction_annotation_terms || []}
          onToggle={(value) => handleArrayToggle("interaction_annotation_terms", value)}
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
