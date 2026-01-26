"use client";

import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useMemo, useState, useEffect } from "react";
import { Network, Tag, Shapes, FileText, Database, FlaskConical, Loader2 } from "lucide-react";
import type { SearchResult } from "./result-card";
import { MoleculeStructure } from "./molecule_structure";
import { InteractionsExploreTab } from "@/features/explore/components/interactions-explore-tab";
import { MeilisearchFilters } from "@/types/meilisearch";
import { searchInteractionsMeilisearch } from "@/lib/meilisearch/search";
import { getEntityTypeEmoji } from "@/lib/utils/entity-types";
import SearchPage from "@/features/search/page";

interface EntityDetailsDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    entity: SearchResult | null;
}

// Helper to detect if entity is a small molecule
const isSmallMolecule = (result: SearchResult): boolean => {
    const entityType = result._formatted?.entity_type || result.entity_type || '';
    const typeLabel = entityType.split(':')[0].toLowerCase().replace(/[\s_]/g, '');
    return typeLabel === 'smallmolecule' ||
        typeLabel === 'compound' ||
        typeLabel === 'metabolite' ||
        typeLabel === 'drug' ||
        typeLabel === 'lipid' ||
        !!(result.canonical_smiles || result.formula || result.molecular_weight);
};

// Entity Card Header Component
function EntityCardHeader({ entity }: { entity: SearchResult }) {
    const entityType = entity._formatted?.entity_type || entity.entity_type;
    const entityTypeLabel = entityType ? entityType.split(':')[0] : "Entity";
    const names = entity._formatted?.names || entity.names || [];
    const geneSymbols = entity._formatted?.gene_symbols || entity.gene_symbols || [];
    const descriptions = entity._formatted?.descriptions || entity.descriptions || [];
    const definition = entity._formatted?.definition || entity.definition;
    const identifiers = entity._formatted?.identifiers || entity.identifiers || [];

    // Get display name
    const displayName = geneSymbols[0] || names[0] || `Entity ${entity.entity_id || entity.id}`;

    // Extract SMILES for molecules
    const smiles = useMemo(() => {
        if (!isSmallMolecule(entity)) return null;
        for (const id of identifiers) {
            const entries = Object.entries(id);
            if (entries.length > 0) {
                const [key, value] = entries[0];
                const idType = key.split(':')[0].toLowerCase().trim();
                if (idType === 'biotin tag' || idType === 'biotin' || idType === 'smiles' || idType === 'canonical_smiles') {
                    return value as string;
                }
            }
        }
        return entity.canonical_smiles || null;
    }, [entity, identifiers]);

    const entityTypeEmoji = getEntityTypeEmoji(entityTypeLabel);

    return (
        <div className="flex gap-4 p-4 bg-muted/20">
            {/* Molecule structure image if applicable */}
            {smiles && (
                <div className="shrink-0">
                    <MoleculeStructure
                        smiles={smiles}
                        width={120}
                        height={100}
                        compoundName={displayName}
                        className="rounded-md"
                    />
                </div>
            )}

            <div className="flex-1 min-w-0">
                {/* Title and type badge */}
                <div className="flex items-start justify-between gap-2 mb-2">
                    <h2 className="text-xl font-semibold truncate">{displayName}</h2>
                    <Badge variant="secondary" className="flex items-center gap-1 shrink-0">
                        {entityTypeEmoji && <span>{entityTypeEmoji}</span>}
                        {entityTypeLabel}
                    </Badge>
                </div>

                {/* Description */}
                {(definition || descriptions[0]) && (
                    <p className="text-sm text-muted-foreground line-clamp-2">
                        {definition || descriptions[0]}
                    </p>
                )}

                {/* Stats row */}
                <div className="flex items-center gap-4 mt-3 text-sm text-muted-foreground">
                    {entity.num_interactions && entity.num_interactions > 0 && (
                        <div className="flex items-center gap-1">
                            <Network className="h-4 w-4" />
                            <span>{entity.num_interactions} interactions</span>
                        </div>
                    )}
                    {entity.complexes && entity.complexes.length > 0 && (
                        <div className="flex items-center gap-1">
                            <Shapes className="h-4 w-4" />
                            <span>{entity.complexes.length} complexes</span>
                        </div>
                    )}
                    {entity.cv_terms && entity.cv_terms.length > 0 && (
                        <div className="flex items-center gap-1">
                            <Tag className="h-4 w-4" />
                            <span>{entity.cv_terms.length} annotations</span>
                        </div>
                    )}
                    {entity.references && entity.references.length > 0 && (
                        <div className="flex items-center gap-1">
                            <FileText className="h-4 w-4" />
                            <span>{entity.references.length} refs</span>
                        </div>
                    )}
                    {entity.sources && entity.sources.length > 0 && (
                        <div className="flex items-center gap-1">
                            <Database className="h-4 w-4" />
                            <span>{entity.sources.length} sources</span>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

export function EntityDetailsDialog({ open, onOpenChange, entity }: EntityDetailsDialogProps) {
    const [activeTab, setActiveTab] = useState("interactions");
    const [interactionsCount, setInteractionsCount] = useState<number | null>(null);
    const [associationsCount, setAssociationsCount] = useState<number>(0);
    const [associatedEntityIds, setAssociatedEntityIds] = useState<number[]>([]);
    const [loadingCounts, setLoadingCounts] = useState(true);

    // Get entity ID
    const entityId = entity?.entity_id ?? (entity?.id ? parseInt(entity.id, 10) : null);
    const entityIds = entityId ? [entityId] : [];

    // Filters for interactions tab
    const [interactionFilters, setInteractionFilters] = useState<MeilisearchFilters>({});

    // Update filters when entity changes
    useEffect(() => {
        if (entityIds.length > 0) {
            setInteractionFilters({ entity_ids: entityIds });
        }
    }, [entityId]);

    // Fetch counts when dialog opens
    useEffect(() => {
        if (!open || !entityId) {
            setInteractionsCount(null);
            setAssociationsCount(0);
            setAssociatedEntityIds([]);
            setLoadingCounts(true);
            return;
        }

        async function fetchCounts() {
            setLoadingCounts(true);
            try {
                // Fetch interactions count
                const interactionsResponse = await searchInteractionsMeilisearch({
                    query: "",
                    index: 'search_interactions' as any,
                    limit: 1,
                    offset: 0,
                    filters: { entity_ids: [entityId!] }
                });
                setInteractionsCount(interactionsResponse.estimatedTotalHits || 0);

                // Fetch associations (bidirectional query like entity-selection-context does)
                const { searchAssociationsMeilisearch } = await import("@/lib/meilisearch/search");
                const [parentsResponse, membersResponse] = await Promise.all([
                    searchAssociationsMeilisearch({
                        query: "",
                        index: 'search_associations' as any,
                        limit: 10000,
                        offset: 0,
                        filters: { member_entity_ids: [entityId!] }
                    }),
                    searchAssociationsMeilisearch({
                        query: "",
                        index: 'search_associations' as any,
                        limit: 10000,
                        offset: 0,
                        filters: { parent_entity_ids: [entityId!] }
                    })
                ]);

                // Extract unique entity IDs
                const entityIdSet = new Set<number>();
                const parentHits = parentsResponse.hits as any[];
                const memberHits = membersResponse.hits as any[];

                parentHits.forEach(hit => {
                    if (hit.parent_entity_id) entityIdSet.add(hit.parent_entity_id);
                });
                memberHits.forEach(hit => {
                    if (hit.member_entity_id) entityIdSet.add(hit.member_entity_id);
                });

                const associatedIds = Array.from(entityIdSet);
                setAssociatedEntityIds(associatedIds);
                setAssociationsCount(associatedIds.length);
            } catch (error) {
                console.error("Error fetching counts:", error);
                setInteractionsCount(0);
                setAssociationsCount(0);
            } finally {
                setLoadingCounts(false);
            }
        }

        fetchCounts();
    }, [open, entityId]);

    // Set default active tab based on what's available
    useEffect(() => {
        if (!loadingCounts) {
            if ((interactionsCount ?? 0) > 0) {
                setActiveTab("interactions");
            } else if (associationsCount > 0) {
                setActiveTab("associations");
            }
        }
    }, [loadingCounts, interactionsCount, associationsCount]);

    if (!entity) return null;

    const hasInteractions = (interactionsCount ?? 0) > 0;
    const hasAssociations = associationsCount > 0;
    const hasAnyTab = hasInteractions || hasAssociations;

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-4xl h-[85vh] flex flex-col p-0 gap-0">
                {/* Visually hidden title for accessibility */}
                <DialogTitle className="sr-only">
                    Entity Details
                </DialogTitle>

                {/* Entity Card Header */}
                <EntityCardHeader entity={entity} />

                {/* Tabs - only show if there's content */}
                {hasAnyTab ? (
                    <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col min-h-0">
                        <div className="px-4">
                            <TabsList className="h-10">
                                {hasInteractions && (
                                    <TabsTrigger value="interactions" className="flex items-center gap-2">
                                        Interactions
                                        {loadingCounts ? (
                                            <Loader2 className="h-3 w-3 animate-spin" />
                                        ) : (
                                            <Badge variant="secondary" className="text-xs">
                                                {interactionsCount?.toLocaleString() || 0}
                                            </Badge>
                                        )}
                                    </TabsTrigger>
                                )}
                                {hasAssociations && (
                                    <TabsTrigger value="associations" className="flex items-center gap-2">
                                        Associations
                                        <Badge variant="secondary" className="text-xs">
                                            {associationsCount}
                                        </Badge>
                                    </TabsTrigger>
                                )}
                            </TabsList>
                        </div>

                        {hasInteractions && (
                            <TabsContent value="interactions" className="flex-1 min-h-0 m-0 overflow-hidden">
                                <div className="h-full overflow-hidden [&>div]:h-full [&>div]:!max-h-full [&_.h-svh]:h-full">
                                    {entityIds.length > 0 && (
                                        <InteractionsExploreTab
                                            filters={interactionFilters}
                                            onFilterChange={setInteractionFilters}
                                            onFilterCountsUpdate={() => { }}
                                        />
                                    )}
                                </div>
                            </TabsContent>
                        )}

                        {hasAssociations && (
                            <TabsContent value="associations" className="flex-1 min-h-0 m-0 overflow-hidden">
                                <SearchPage
                                    embedded={true}
                                    allowOntologyInEmbedded={false}
                                    showLayoutSwitcherInEmbedded={false}
                                    showFilters={false}
                                    initialFilters={{ entity_ids: associatedEntityIds }}
                                />
                            </TabsContent>
                        )}
                    </Tabs>
                ) : (
                    <div className="flex-1 flex items-center justify-center text-muted-foreground">
                        {loadingCounts ? (
                            <div className="flex items-center gap-2">
                                <Loader2 className="h-4 w-4 animate-spin" />
                                Loading...
                            </div>
                        ) : (
                            "No interactions or associations found"
                        )}
                    </div>
                )}
            </DialogContent>
        </Dialog>
    );
}

