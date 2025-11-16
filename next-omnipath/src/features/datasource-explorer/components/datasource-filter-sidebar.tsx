"use client"

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Filter, RotateCcw } from "lucide-react"
import { cn } from "@/lib/utils"
import { DatasourceSearchBar } from "./datasource-search-bar"
import {
  DataSourceFilters,
  CATEGORIES,
  ENTITY_TYPES,
  UPDATE_CATEGORIES,
  ACCESS_CATEGORIES,
  HEALTH_STATUSES,
  LICENSE_TYPES,
  EVIDENCE_LEVELS,
  TAXON_SCOPES
} from "../types/datasource"
import {
  INTERACTION_TYPES,
  ANNOTATION_TYPES,
  ONTOLOGY_TYPES
} from "../datasource-new/types"

interface FilterOption {
  value: string;
  label: string;
  count?: number;
  description?: string;
  color?: string;
  icon?: string;
}

interface DatasourceFilterSidebarProps {
  filters: DataSourceFilters;
  onFilterChange: (filters: DataSourceFilters) => void;
  onClearFilters: () => void;
  datasourceCounts?: {
    categories: Record<string, number>;
    entityTypes: Record<string, number>;
    updateCategories: Record<string, number>;
    accessCategories: Record<string, number>;
    healthStatuses: Record<string, number>;
    licenseTypes: Record<string, number>;
    evidenceLevels: Record<string, number>;
    taxonScopes: Record<string, number>;
    interactionTypes: Record<string, number>;
    annotationTypes: Record<string, number>;
    ontologyTypes: Record<string, number>;
  };
  isMobile?: boolean;
}

function FilterSection({ 
  title, 
  filterKey, 
  options, 
  selectedValues, 
  onToggle,
  showDescription = false
}: {
  title: string;
  filterKey: keyof DataSourceFilters;
  options: FilterOption[];
  selectedValues: string[];
  onToggle: (value: string) => void;
  showDescription?: boolean;
}) {
  if (options.length === 0) return null;

  return (
    <AccordionItem value={filterKey}>
      <AccordionTrigger>{title}</AccordionTrigger>
      <AccordionContent>
        <div className="space-y-1 max-h-64 overflow-y-auto pr-2">
          {options.map(({ value, label, count, description, color, icon }) => {
            const isSelected = selectedValues?.includes(value) || false;
            
            return (
              <div key={value} className="py-0.5">
                <div className="flex items-center justify-between group gap-2">
                  <Label
                    htmlFor={`${filterKey}-${value}`}
                    className={cn(
                      "flex items-center gap-1.5 text-xs font-normal cursor-pointer group-hover:text-primary transition-colors min-w-0 flex-1",
                      isSelected && "text-primary font-medium"
                    )}
                  >
                    <Checkbox
                      id={`${filterKey}-${value}`}
                      checked={isSelected}
                      onCheckedChange={() => onToggle(value)}
                      className={cn(
                        "h-3.5 w-3.5 flex-shrink-0",
                        isSelected && "border-primary"
                      )}
                    />
                    <span className="flex items-center gap-1 truncate">
                      {icon && <span>{icon}</span>}
                      <span className={color}>{label}</span>
                    </span>
                  </Label>
                  {count !== undefined && (
                    <Badge 
                      variant={isSelected ? "default" : "outline"} 
                      className={cn(
                        "text-xs h-5 px-1.5 py-0 transition-colors flex-shrink-0",
                        "group-hover:bg-primary/10",
                        isSelected && "bg-primary text-primary-foreground"
                      )}
                    >
                      {count}
                    </Badge>
                  )}
                </div>
                {showDescription && description && (
                  <p className="text-xs text-muted-foreground ml-5 mt-0.5">{description}</p>
                )}
              </div>
            );
          })}
        </div>
      </AccordionContent>
    </AccordionItem>
  );
}

export function DatasourceFilterSidebar({
  filters,
  onFilterChange,
  onClearFilters,
  datasourceCounts,
  isMobile = false,
}: DatasourceFilterSidebarProps) {
  // Calculate active filter count
  const activeFilterCount = Object.entries(filters).reduce((count, [key, value]) => {
    if (key === 'search') return count; // Don't count search as a filter
    if (Array.isArray(value)) return count + value.length;
    if (value !== null && value !== undefined) return count + 1;
    return count;
  }, 0);

  const handleSearch = (query: string) => {
    onFilterChange({
      ...filters,
      search: query || undefined
    });
  };

  // Handler for toggling filters
  const handleToggle = (filterKey: keyof DataSourceFilters, value: string) => {
    const currentValues = (filters[filterKey] as string[]) || [];
    const newValues = currentValues.includes(value)
      ? currentValues.filter(v => v !== value)
      : [...currentValues, value];
    
    onFilterChange({
      ...filters,
      [filterKey]: newValues.length > 0 ? newValues : undefined,
    });
  };

  // Transform options with counts
  const getOptionsWithCounts = (
    items: ReadonlyArray<{ value: string; label: string; description?: string; color?: string; icon?: string }>,
    counts: Record<string, number> | undefined
  ): FilterOption[] => {
    return items.map(item => ({
      ...item,
      count: counts?.[item.value] || 0
    }));
  };

  const content = (
    <div className="space-y-4">
      {/* Search Bar */}
      <div className="mb-4">
        <Label className="text-sm font-medium mb-2 block">Search</Label>
        <DatasourceSearchBar
          placeholder="Search datasources..."
          onSearch={handleSearch}
          initialQuery={filters.search || ""}
        />
      </div>

      <Accordion 
        type="multiple" 
        defaultValue={[
          "categories", 
          "entityTypes",
          "interactionTypes",
          "annotationTypes",
          "ontologyTypes"
        ]} 
        className="w-full"
      >
        <FilterSection
          title="Categories"
          filterKey="categories"
          options={getOptionsWithCounts(CATEGORIES, datasourceCounts?.categories)}
          selectedValues={filters.categories || []}
          onToggle={(value) => handleToggle("categories", value)}
        />

        {/* Conditional type filters based on selected categories */}
        {filters.categories?.includes("interaction") && (
          <FilterSection
            title="Interaction Types"
            filterKey="interactionTypes"
            options={getOptionsWithCounts(
              INTERACTION_TYPES.map(type => ({ 
                value: type, 
                label: type.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()) 
              })),
              datasourceCounts?.interactionTypes
            )}
            selectedValues={filters.interactionTypes || []}
            onToggle={(value) => handleToggle("interactionTypes", value)}
          />
        )}

        {filters.categories?.includes("annotation") && (
          <FilterSection
            title="Annotation Types"
            filterKey="annotationTypes"
            options={getOptionsWithCounts(
              ANNOTATION_TYPES.map(type => ({ 
                value: type, 
                label: type.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()) 
              })),
              datasourceCounts?.annotationTypes
            )}
            selectedValues={filters.annotationTypes || []}
            onToggle={(value) => handleToggle("annotationTypes", value)}
          />
        )}

        {filters.categories?.includes("ontology") && (
          <FilterSection
            title="Ontology Types"
            filterKey="ontologyTypes"
            options={getOptionsWithCounts(
              ONTOLOGY_TYPES.map(type => ({ 
                value: type, 
                label: type.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()) 
              })),
              datasourceCounts?.ontologyTypes
            )}
            selectedValues={filters.ontologyTypes || []}
            onToggle={(value) => handleToggle("ontologyTypes", value)}
          />
        )}

        <FilterSection
          title="Entity Types"
          filterKey="entityTypes"
          options={getOptionsWithCounts(ENTITY_TYPES, datasourceCounts?.entityTypes)}
          selectedValues={filters.entityTypes || []}
          onToggle={(value) => handleToggle("entityTypes", value)}
        />

        <FilterSection
          title="Update Frequency"
          filterKey="updateCategories"
          options={getOptionsWithCounts(UPDATE_CATEGORIES, datasourceCounts?.updateCategories)}
          selectedValues={filters.updateCategories || []}
          onToggle={(value) => handleToggle("updateCategories", value)}
          showDescription={true}
        />

        <FilterSection
          title="Access Method"
          filterKey="accessCategories"
          options={getOptionsWithCounts(ACCESS_CATEGORIES, datasourceCounts?.accessCategories)}
          selectedValues={filters.accessCategories || []}
          onToggle={(value) => handleToggle("accessCategories", value)}
          showDescription={true}
        />

        <FilterSection
          title="Health Status"
          filterKey="healthStatuses"
          options={getOptionsWithCounts(HEALTH_STATUSES, datasourceCounts?.healthStatuses)}
          selectedValues={filters.healthStatuses || []}
          onToggle={(value) => handleToggle("healthStatuses", value)}
        />

        <FilterSection
          title="License Type"
          filterKey="licenseTypes"
          options={getOptionsWithCounts(LICENSE_TYPES, datasourceCounts?.licenseTypes)}
          selectedValues={filters.licenseTypes || []}
          onToggle={(value) => handleToggle("licenseTypes", value)}
        />

        <FilterSection
          title="Evidence Level"
          filterKey="evidenceLevels"
          options={getOptionsWithCounts(EVIDENCE_LEVELS, datasourceCounts?.evidenceLevels)}
          selectedValues={filters.evidenceLevels || []}
          onToggle={(value) => handleToggle("evidenceLevels", value)}
        />

        <FilterSection
          title="Taxon Scope"
          filterKey="taxonScopes"
          options={getOptionsWithCounts(TAXON_SCOPES, datasourceCounts?.taxonScopes)}
          selectedValues={filters.taxonScopes || []}
          onToggle={(value) => handleToggle("taxonScopes", value)}
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
          {activeFilterCount > 0 && (
            <Button 
              variant="ghost" 
              size="sm" 
              onClick={onClearFilters} 
              className="flex items-center gap-1 text-muted-foreground hover:text-foreground"
            >
              <RotateCcw className="h-4 w-4" />
              Clear ({activeFilterCount})
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