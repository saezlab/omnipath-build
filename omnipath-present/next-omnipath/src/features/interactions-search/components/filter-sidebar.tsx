"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { MeilisearchFilters } from "@/types/meilisearch"
import { ArrowRight, Plus, Minus, X, Filter, Search } from "lucide-react"
import { cn, formatNumber, getEntityTypeEmoji } from "@/lib/utils"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { EntityHoverCard, CvTermHoverCard } from "@/features/search/components/result-card"

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

function extractTermId(value: string): string | null {
  // Match ontology term IDs (PSI-MI, OmniPath, GO, etc.)
  const match = value.match(/(MI|OM|GO|HP|KW|DO|MP|CHEBI|CL|UBERON|MONDO):\d{4,}/);
  return match ? match[0] : null;
}

const PREFIX_NAMES: Record<string, string> = {
  GO: "Gene Ontology",
  MI: "Molecular Interactions",
  OM: "OmniPath Terms",
  KW: "UniProt Keywords",
  DO: "Disease Ontology",
  HP: "Human Phenotype",
  CHEBI: "ChEBI",
  CL: "Cell Ontology",
  UBERON: "Uberon",
  MONDO: "Mondo",
};

const ENTITY_ONTOLOGY_FACET_MAP: Record<string, string> = {
  GO: "cv_terms_go",
  MI: "cv_terms_mi",
  OM: "cv_terms_om",
  HP: "cv_terms_hp",
  KW: "cv_terms_kw",
};

function extractPrefix(termId: string): string {
  const match = termId.match(/^([A-Z]{2,}):/);
  return match ? match[1] : "OTHER";
}

function getEntityFilterKeyForValue(value: string): keyof MeilisearchFilters | null {
  const prefix = extractPrefix(value);
  const keyByPrefix: Record<string, keyof MeilisearchFilters> = {
    GO: "cv_terms_go",
    MI: "cv_terms_mi",
    OM: "cv_terms_om",
    HP: "cv_terms_hp",
    KW: "cv_terms_kw",
  };
  return keyByPrefix[prefix] ?? null;
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
          {options.map((option) => (
            <FilterOptionRow
              key={option.value}
              filterKey={filterKey}
              option={option}
              selectedValues={selectedValues}
              onToggle={onToggle}
              showHoverCard={showHoverCard}
              showIcon={showIcon}
            />
          ))}
        </div>
      </AccordionContent>
    </AccordionItem>
  );
}

interface FilterOptionRowProps {
  filterKey: keyof MeilisearchFilters;
  option: FilterOption;
  selectedValues: string[];
  onToggle: (value: string) => void;
  showHoverCard?: boolean;
  showIcon?: boolean;
  labelOverride?: string;
  highlighted?: boolean;
}

function FilterOptionRow({
  filterKey,
  option,
  selectedValues,
  onToggle,
  showHoverCard = false,
  showIcon = false,
  labelOverride,
  highlighted = false,
}: FilterOptionRowProps) {
  const { value, count, label, icon } = option;
  const isSelected = selectedValues?.includes(value) || false;
  // Parse label and ID from "Label:ID" format if present
  // If the value matches the pattern "Label:Prefix:ID", we want to split correctly
  // Example: "Agonist:MI:0001" -> label="Agonist", id="MI:0001"
  let displayLabel = labelOverride || label;
  let entityId: string | null = extractTermId(value);

  if (!displayLabel) {
    // Try to parse from value string
    const parts = value.split(':');
    if (parts.length >= 2) {
      // Check if it looks like a CV term ID (MI:xxxx or OM:xxxx)
      const possiblePrefix = parts[parts.length - 2];
      if (['MI', 'OM'].includes(possiblePrefix)) {
        // Format: "Label:MI:0001"
        entityId = `${parts[parts.length - 2]}:${parts[parts.length - 1]}`;
        displayLabel = parts.slice(0, parts.length - 2).join(':');
      } else {
        // Format: "Label:ID" (standard entity)
        entityId = parts[parts.length - 1];
        displayLabel = parts.slice(0, parts.length - 1).join(':');
      }
    } else {
      displayLabel = value;
    }
  } else {
    // Label is provided, try to extract ID from value if it looks like an ID
    // If value already contains the ID (which is typical), we need to extract the ID part
    const parts = value.split(':');
    if (parts.length >= 2 && !entityId) {
      const possiblePrefix = parts[parts.length - 2];
      if (['MI', 'OM'].includes(possiblePrefix)) {
        entityId = `${parts[parts.length - 2]}:${parts[parts.length - 1]}`;
      } else {
        entityId = parts[parts.length - 1];
      }
    }
  }

  const labelContent = (
    <span className={cn(
      "truncate",
      highlighted ? "text-primary font-medium" : ""
    )}>
      {showIcon && icon && <span className="mr-1.5">{icon}</span>}
      {displayLabel}
    </span>
  );

  const isCvTerm = !!(entityId && extractTermId(entityId));

  return (
    <div className="flex items-center justify-between group py-0.5 gap-2">
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
          isCvTerm ? (
            <CvTermHoverCard termId={entityId}>
              {labelContent}
            </CvTermHoverCard>
          ) : (
            <EntityHoverCard entityId={entityId}>
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
        const termId = extractTermId(value);
        const label = termId && termId === value
          ? value
          : value.includes(':')
            ? value.split(':')[0]
            : value;
        // Get emoji icon for member_types and sources filters
        const icon = filterKey === 'member_types'
          ? getEntityTypeEmoji(value)
          : filterKey === 'sources'
            ? '📚'
            : undefined;
        return {
          value,
          count,
          label,
          icon
        };
      })
      .sort((a, b) => b.count - a.count);
  };

  const content = (
    <div className="space-y-4">
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
        {/* Sources Filter */}
        <ArrayFilterSection
          title="Sources"
          filterKey="sources"
          options={transformFilterCounts(filterCounts.sources, 'sources')}
          selectedValues={filters.sources || []}
          onToggle={(value) => handleArrayToggle("sources", value)}
          showHoverCard={true}
          showIcon={true}
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

type AnnotationFilterSidebarProps =
  | {
      mode: "entities";
      filters: MeilisearchFilters;
      onFilterChange: (filters: MeilisearchFilters) => void;
      ontologyFacetCountsByPrefix: Record<string, Record<string, number>>;
      isMobile?: boolean;
    }
  | {
      mode: "interactions";
      filters: MeilisearchFilters;
      filterCounts: Record<string, Record<string, number>>;
      onFilterChange: (filters: MeilisearchFilters) => void;
      isMobile?: boolean;
    };

interface OntologyTabGroup {
  prefix: string;
  name: string;
  termIds: string[];
  terms: FilterOption[];
  totalCount: number;
  unmatched: FilterOption[];
}

interface FilteredOntologyTab extends OntologyTabGroup {
  filteredTerms?: FilterOption[];
  filteredUnmatched?: FilterOption[];
  hasMatches: boolean;
}

export function AnnotationFilterSidebar({
  mode,
  filters,
  onFilterChange,
  isMobile = false,
  ...rest
}: AnnotationFilterSidebarProps) {
  const [annotationQuery, setAnnotationQuery] = useState("");
  const [activeTab, setActiveTab] = useState<string>("");
  const [facetSearchCountsByPrefix, setFacetSearchCountsByPrefix] = useState<Record<string, Record<string, number>>>({});
  const [facetSearchCounts, setFacetSearchCounts] = useState<Record<string, number>>({});

  const annotationOptions = useMemo(() => {
    if (mode === "entities") {
      const countsByPrefix = annotationQuery.trim()
        ? facetSearchCountsByPrefix
        : rest.ontologyFacetCountsByPrefix;
      return Object.entries(countsByPrefix)
        .flatMap(([prefix, counts]) =>
          Object.entries(counts).map(([value, count]) => ({
            value,
            count,
            label: value,
            icon: prefix,
          }))
        )
        .sort((a, b) => b.count - a.count);
    }

    const counts = annotationQuery.trim()
      ? facetSearchCounts
      : rest.filterCounts.interaction_annotation_terms;
    if (!counts) return [];
    return Object.entries(counts)
      .map(([value, count]) => {
        const termId = extractTermId(value);
        const label = termId && termId === value
          ? value
          : value.includes(':')
            ? value.split(':')[0]
            : value;
        return {
          value,
          count,
          label
        };
      })
      .sort((a, b) => b.count - a.count);
  }, [annotationQuery, facetSearchCounts, facetSearchCountsByPrefix, mode, rest]);

  const annotationTermOptions = useMemo(() => {
    const mapped = new Map<string, FilterOption>();
    const unmatched: FilterOption[] = [];

    for (const option of annotationOptions) {
      const termId = extractTermId(option.value);
      if (termId) {
        mapped.set(termId, option);
      } else {
        if (mode === "interactions") {
          unmatched.push(option);
        }
      }
    }

    return { mapped, unmatched };
  }, [annotationOptions, mode]);

  const termsByPrefix = useMemo(() => {
    if (mode === "entities") {
      const groups = new Map<string, { termIds: string[]; totalCount: number }>();
      const countsByPrefix = annotationQuery.trim()
        ? facetSearchCountsByPrefix
        : rest.ontologyFacetCountsByPrefix;
      Object.entries(countsByPrefix).forEach(([prefix, counts]) => {
        const termIds = Object.keys(counts);
        if (termIds.length === 0) return;
        const totalCount = termIds.reduce((sum, termId) => sum + (counts[termId] || 0), 0);
        groups.set(prefix, { termIds, totalCount });
      });
      return groups;
    }

    const groups = new Map<string, { termIds: string[]; totalCount: number }>();
    for (const [termId, option] of annotationTermOptions.mapped.entries()) {
      const prefix = extractPrefix(termId);
      const group = groups.get(prefix) ?? { termIds: [], totalCount: 0 };
      group.termIds.push(termId);
      group.totalCount += option.count;
      groups.set(prefix, group);
    }
    return groups;
  }, [
    annotationQuery,
    annotationTermOptions.mapped,
    facetSearchCountsByPrefix,
    mode,
    "ontologyFacetCountsByPrefix" in rest ? rest.ontologyFacetCountsByPrefix : undefined,
  ]);

  const unmatchedTotalCount = useMemo(
    () => annotationTermOptions.unmatched.reduce((sum, option) => sum + option.count, 0),
    [annotationTermOptions.unmatched]
  );

  useEffect(() => {
    const trimmedQuery = annotationQuery.trim();
    if (!trimmedQuery) {
      setFacetSearchCountsByPrefix({});
      setFacetSearchCounts({});
      return;
    }

    let cancelled = false;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        if (mode === "entities") {
          const availablePrefixes = Object.keys(rest.ontologyFacetCountsByPrefix || {});
          const fallbackPrefix = availablePrefixes[0] || Object.keys(ENTITY_ONTOLOGY_FACET_MAP)[0];
          const selectedPrefix =
            (activeTab && ENTITY_ONTOLOGY_FACET_MAP[activeTab] ? activeTab : fallbackPrefix) || "";
          const facetName = ENTITY_ONTOLOGY_FACET_MAP[selectedPrefix];

          if (!facetName) {
            if (!cancelled) {
              setFacetSearchCountsByPrefix({});
            }
            return;
          }

          const response = await fetch("/api/meilisearch/facet-search", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              facetName,
              facetQuery: trimmedQuery,
              filters,
              limit: 100,
            }),
            signal: controller.signal,
          });

          if (!response.ok) {
            if (!cancelled) {
              setFacetSearchCountsByPrefix({ [selectedPrefix]: {} });
            }
            return;
          }

          const data = (await response.json()) as { facetHits?: { value: string; count: number }[] };
          const counts: Record<string, number> = {};
          (data.facetHits || []).forEach((hit) => {
            counts[hit.value] = hit.count;
          });

          if (!cancelled) {
            setFacetSearchCountsByPrefix({ [selectedPrefix]: counts });
          }
          return;
        }

        const response = await fetch("/api/meilisearch/facet-search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            facetName: "interaction_annotation_terms",
            facetQuery: trimmedQuery,
            filters,
            index: "interactions",
            limit: 100,
          }),
          signal: controller.signal,
        });

        if (!response.ok) {
          if (!cancelled) {
            setFacetSearchCounts({});
          }
          return;
        }

        const data = (await response.json()) as { facetHits?: { value: string; count: number }[] };
        const counts: Record<string, number> = {};
        (data.facetHits || []).forEach((hit) => {
          counts[hit.value] = hit.count;
        });

        if (!cancelled) {
          setFacetSearchCounts(counts);
        }
      } catch (error) {
        if (!cancelled) {
          setFacetSearchCountsByPrefix({});
          setFacetSearchCounts({});
        }
      }
    }, 250);

    return () => {
      cancelled = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [
    activeTab,
    annotationQuery,
    filters,
    mode,
    "ontologyFacetCountsByPrefix" in rest ? rest.ontologyFacetCountsByPrefix : undefined,
  ]);

  const ontologyTabs = useMemo(() => {
    const tabs: OntologyTabGroup[] = [];

    for (const [prefix, group] of termsByPrefix.entries()) {
      if (group.totalCount <= 0 || group.termIds.length === 0) {
        continue;
      }
      const termOptions = new Map<string, FilterOption>();
      const terms: FilterOption[] = [];

      group.termIds.forEach((termId) => {
        const option = annotationTermOptions.mapped.get(termId);
        if (option) {
          termOptions.set(termId, option);
          terms.push(option);
        }
      });

      tabs.push({
        prefix,
        name: PREFIX_NAMES[prefix] || prefix,
        termIds: group.termIds,
        terms,
        totalCount: group.totalCount,
        unmatched: [],
      });
    }

    if (mode === "interactions" && annotationTermOptions.unmatched.length > 0) {
      tabs.push({
        prefix: "OTHER",
        name: "Other",
        termIds: [],
        terms: [],
        totalCount: unmatchedTotalCount,
        unmatched: annotationTermOptions.unmatched,
      });
    }

    tabs.sort((a, b) => {
      if (a.prefix === "OTHER") return 1;
      if (b.prefix === "OTHER") return -1;
      return b.totalCount - a.totalCount;
    });

    return tabs;
  }, [annotationTermOptions.mapped, annotationTermOptions.unmatched, termsByPrefix, unmatchedTotalCount]);

  const normalizedQuery = annotationQuery.trim().toLowerCase();
  const matchesQuery = useCallback(
    (value?: string) => {
      if (!normalizedQuery) return false;
      return (value || "").toLowerCase().includes(normalizedQuery);
    },
    [normalizedQuery]
  );

  const filteredTabs = useMemo<FilteredOntologyTab[]>(() => {
    return ontologyTabs.map((tab) => {
      if (mode === "entities" && annotationQuery.trim()) {
        return {
          ...tab,
          filteredUnmatched: [],
          filteredTerms: tab.terms,
          hasMatches: tab.terms.length > 0,
        };
      }

      if (!normalizedQuery) {
        return {
          ...tab,
          filteredUnmatched: tab.unmatched,
          hasMatches: tab.unmatched.length > 0 || tab.terms.length > 0,
        };
      }

      const filteredTerms = tab.terms.filter((term) =>
        matchesQuery(term.label || term.value)
      );

      const filteredUnmatched = tab.unmatched.filter((option) =>
        matchesQuery(option.label || option.value)
      );

      const hasMatches =
        filteredUnmatched.length > 0 ||
        filteredTerms.length > 0;

      return {
        ...tab,
        filteredUnmatched,
        filteredTerms,
        hasMatches,
      };
    });
  }, [annotationQuery, matchesQuery, mode, normalizedQuery, ontologyTabs]);

  useEffect(() => {
    const visibleTabs = normalizedQuery
      ? filteredTabs.filter((tab) => tab.hasMatches)
      : filteredTabs;

    if (visibleTabs.length === 0) {
      setActiveTab("");
      return;
    }

    if (!activeTab || !visibleTabs.some((tab) => tab.prefix === activeTab)) {
      setActiveTab(visibleTabs[0].prefix);
      return;
    }

    if (normalizedQuery) {
      const firstMatchingTab = visibleTabs.find((tab) => tab.hasMatches);
      if (firstMatchingTab && firstMatchingTab.prefix !== activeTab) {
        setActiveTab(firstMatchingTab.prefix);
      }
    }
  }, [activeTab, filteredTabs, normalizedQuery]);

  const handleAnnotationToggle = (value: string) => {
    if (mode === "entities") {
      const prefix = extractPrefix(value);
      const filterKeyByPrefix: Record<string, keyof MeilisearchFilters> = {
        GO: "cv_terms_go",
        MI: "cv_terms_mi",
        OM: "cv_terms_om",
        HP: "cv_terms_hp",
        KW: "cv_terms_kw",
      };
      const filterKey = filterKeyByPrefix[prefix];
      if (!filterKey) return;

      const currentValues = (filters[filterKey] as string[] | undefined) || [];
      const newValues = currentValues.includes(value)
        ? currentValues.filter((v) => v !== value)
        : [...currentValues, value];

      onFilterChange({
        ...filters,
        [filterKey]: newValues.length > 0 ? newValues : undefined,
      });
      return;
    }

    const currentValues = filters.interaction_annotation_terms || [];
    const newValues = currentValues.includes(value)
      ? currentValues.filter((v) => v !== value)
      : [...currentValues, value];

    onFilterChange({
      ...filters,
      interaction_annotation_terms: newValues.length > 0 ? newValues : undefined,
    });
  };

  const renderTabContent = (tab: FilteredOntologyTab) => {
    const unmatched: FilterOption[] = tab.filteredUnmatched ?? tab.unmatched;
    const flatTerms: FilterOption[] = normalizedQuery ? tab.filteredTerms ?? [] : tab.terms;
    const hasFlatTerms = flatTerms.length > 0;
    const hasAnyContent = hasFlatTerms || unmatched.length > 0;

    if (!hasAnyContent) {
      return normalizedQuery ? (
        <div className="text-sm text-muted-foreground">
          No ontology terms match your search.
        </div>
      ) : null;
    }

    return (
      <>
        {hasFlatTerms ? (
          <div className="space-y-1">
            {flatTerms.map((option) => (
              (() => {
                if (mode === "entities") {
                  const filterKey = getEntityFilterKeyForValue(option.value);
                  if (!filterKey) return null;
                  return (
                    <FilterOptionRow
                      key={option.value}
                      filterKey={filterKey}
                      option={option}
                      selectedValues={(filters[filterKey] as string[] | undefined) || []}
                      onToggle={handleAnnotationToggle}
                      showHoverCard={true}
                      highlighted={matchesQuery(option.label || option.value)}
                    />
                  );
                }

                return (
                  <FilterOptionRow
                    key={option.value}
                    filterKey="interaction_annotation_terms"
                    option={option}
                    selectedValues={filters.interaction_annotation_terms || []}
                    onToggle={handleAnnotationToggle}
                    showHoverCard={true}
                    highlighted={matchesQuery(option.label || option.value)}
                  />
                );
              })()
            ))}
          </div>
        ) : null}

        {unmatched.length > 0 ? (
          <div className={cn("space-y-1", hasFlatTerms ? "pt-2 border-t border-muted/60" : "")}>
            {unmatched.map((option) => (
              <FilterOptionRow
                key={option.value}
                filterKey="interaction_annotation_terms"
                option={option}
                selectedValues={filters.interaction_annotation_terms || []}
                onToggle={handleAnnotationToggle}
                showHoverCard={true}
                highlighted={matchesQuery(option.label || option.value)}
              />
            ))}
          </div>
        ) : null}
      </>
    );
  };

  const content = (
    <div className="space-y-6">
      <div className="space-y-2">
        <Label className="text-xs font-medium text-muted-foreground">Ontology search</Label>
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Filter ontology terms"
            value={annotationQuery}
            onChange={(event) => setAnnotationQuery(event.target.value)}
            className="pl-9 pr-9 h-9"
          />
          {annotationQuery ? (
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setAnnotationQuery("")}
              className="absolute right-1 top-1/2 -translate-y-1/2 h-7 w-7"
            >
              <X className="h-4 w-4" />
            </Button>
          ) : null}
        </div>
      </div>
      {filteredTabs.length > 0 ? (
        <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
          <TabsList className="flex flex-wrap h-auto gap-1 bg-muted/50 p-1">
            {(normalizedQuery ? filteredTabs.filter((tab) => tab.hasMatches) : filteredTabs).map((tab) => (
              <TabsTrigger
                key={tab.prefix}
                value={tab.prefix}
                className={cn(
                  "flex items-center gap-2 px-3 py-1.5 text-xs",
                  "data-[state=active]:bg-background data-[state=active]:shadow-sm"
                )}
              >
                <span>{tab.name}</span>
                <Badge variant="secondary" className="ml-1 text-[11px]">
                  {formatNumber(tab.termIds.length || tab.unmatched.length)}
                </Badge>
              </TabsTrigger>
            ))}
          </TabsList>
          {(normalizedQuery ? filteredTabs.filter((tab) => tab.hasMatches) : filteredTabs).map((tab) => (
            <TabsContent key={tab.prefix} value={tab.prefix} className="mt-4">
              {renderTabContent(tab)}
            </TabsContent>
          ))}
        </Tabs>
      ) : (
        <div className="text-sm text-muted-foreground">
          No ontology terms available.
        </div>
      )}
    </div>
  );

  if (isMobile) {
    return content;
  }

  return (
    <Card className="h-full overflow-hidden flex flex-col">
      <CardHeader className="border-b flex-shrink-0 h-[57px] flex items-center py-3">
        <div className="flex items-center gap-2">
          <Filter className="h-5 w-5 text-primary" />
          <h3 className="font-semibold text-lg">Ontology Browser</h3>
        </div>
      </CardHeader>
      <CardContent className="flex-1 min-h-0 overflow-y-auto py-4">
        {content}
      </CardContent>
    </Card>
  );
}
