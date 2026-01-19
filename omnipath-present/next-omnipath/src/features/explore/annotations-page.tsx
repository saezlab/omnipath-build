"use client";

import { useSidebarContent } from "@/contexts/sidebar-content-context";
import { useEffect, useState, useMemo } from "react";
import { useEntitySelection } from "@/contexts/entity-selection-context";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { CvTermHoverCard } from "@/features/search/components/result-card";
import { formatNumber, cn } from "@/lib/utils";

interface TreeNode {
    id: string;
    name?: string;
    distance?: number;
    children?: TreeNode[];
}

interface AnnotationParentGroup {
    id: string;
    name: string;
    terms: { termId: string; count: number }[];
}

interface AnnotationBranchGroup {
    id: string;
    name: string;
    parents: AnnotationParentGroup[];
    totalCount: number;
}

interface OntologyTabGroup {
    prefix: string;
    name: string;
    termIds: string[];
    totalCount: number;
    tree: TreeNode | null;
    branches: AnnotationBranchGroup[];
}

// Map prefixes to ontology display names
const PREFIX_NAMES: Record<string, string> = {
    'GO': 'Gene Ontology',
    'MI': 'Molecular Interactions',
    'OM': 'OmniPath Terms',
    'KW': 'UniProt Keywords',
    'DO': 'Disease Ontology',
    'HP': 'Human Phenotype',
    'CHEBI': 'ChEBI',
};

function extractPrefix(termId: string): string {
    const match = termId.match(/^([A-Z]{2,}):/);
    return match ? match[1] : 'OTHER';
}

export default function AnnotationsPage() {
    const { setSidebarContent } = useSidebarContent();
    const { selectedEntities } = useEntitySelection();
    const [ontologyTrees, setOntologyTrees] = useState<Record<string, TreeNode | null>>({});
    const [loading, setLoading] = useState(false);
    const [activeTab, setActiveTab] = useState<string>("");

    // Aggregate CV term accessions from selected entities
    const cvTermCounts = useMemo(() => {
        const counts = new Map<string, number>();
        for (const entity of selectedEntities) {
            const cvTerms = entity.cv_terms || entity.fullResult?.cv_terms || [];
            for (const term of cvTerms) {
                counts.set(term, (counts.get(term) || 0) + 1);
            }
        }
        return counts;
    }, [selectedEntities]);

    // Group terms by ontology prefix
    const termsByPrefix = useMemo(() => {
        const groups = new Map<string, { termIds: string[]; totalCount: number }>();
        for (const [termId, count] of cvTermCounts.entries()) {
            const prefix = extractPrefix(termId);
            if (!groups.has(prefix)) {
                groups.set(prefix, { termIds: [], totalCount: 0 });
            }
            const group = groups.get(prefix)!;
            group.termIds.push(termId);
            group.totalCount += count;
        }
        return groups;
    }, [cvTermCounts]);

    // Fetch ontology trees for each prefix group
    useEffect(() => {
        if (termsByPrefix.size === 0) {
            setOntologyTrees({});
            return;
        }

        setLoading(true);
        const loadTrees = async () => {
            const trees: Record<string, TreeNode | null> = {};

            for (const [prefix, group] of termsByPrefix.entries()) {
                try {
                    const response = await fetch("/api/ontology/tree", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ termIds: group.termIds }),
                    });

                    if (response.ok) {
                        const data = await response.json();
                        trees[prefix] = data.root || null;
                    } else {
                        trees[prefix] = null;
                    }
                } catch {
                    trees[prefix] = null;
                }
            }

            setOntologyTrees(trees);
            setLoading(false);
        };

        loadTrees();
    }, [termsByPrefix]);

    // Build tab groups with tree structures
    const ontologyTabs = useMemo(() => {
        const tabs: OntologyTabGroup[] = [];

        for (const [prefix, group] of termsByPrefix.entries()) {
            const tree = ontologyTrees[prefix];
            const branches: AnnotationBranchGroup[] = [];

            if (tree) {
                // Build hierarchical structure from tree
                const parentById = new Map<string, TreeNode>();
                const rootChildById = new Map<string, TreeNode>();
                const nameById = new Map<string, string>();

                const visit = (node: TreeNode, parent: TreeNode | null, rootChild: TreeNode | null) => {
                    nameById.set(node.id, node.name || node.id);
                    if (parent) parentById.set(node.id, parent);
                    if (rootChild) rootChildById.set(node.id, rootChild);
                    node.children?.forEach((child) => {
                        visit(child, node, rootChild ?? child);
                    });
                };
                visit(tree, null, null);

                const branchMap = new Map<string, AnnotationBranchGroup>();
                const ensureBranch = (id: string, name: string) => {
                    if (!branchMap.has(id)) {
                        branchMap.set(id, { id, name, parents: [], totalCount: 0 });
                    }
                    return branchMap.get(id)!;
                };

                const ensureParent = (branch: AnnotationBranchGroup, id: string, name: string) => {
                    let parentGroup = branch.parents.find((p) => p.id === id);
                    if (!parentGroup) {
                        parentGroup = { id, name, terms: [] };
                        branch.parents.push(parentGroup);
                    }
                    return parentGroup;
                };

                for (const termId of group.termIds) {
                    const count = cvTermCounts.get(termId) || 0;
                    const parent = parentById.get(termId);
                    const rootChild = rootChildById.get(termId);
                    const branch = rootChild
                        ? ensureBranch(rootChild.id, nameById.get(rootChild.id) || rootChild.id)
                        : ensureBranch("other", "Other");

                    branch.totalCount += count;
                    const parentGroup = parent
                        ? ensureParent(branch, parent.id, nameById.get(parent.id) || parent.id)
                        : ensureParent(branch, "other", "Other");
                    parentGroup.terms.push({ termId, count });
                }

                // Sort branches and terms
                for (const branch of branchMap.values()) {
                    branch.parents.forEach(p => p.terms.sort((a, b) => b.count - a.count));
                    branch.parents.sort((a, b) =>
                        b.terms.reduce((s, t) => s + t.count, 0) - a.terms.reduce((s, t) => s + t.count, 0)
                    );
                    branches.push(branch);
                }
                branches.sort((a, b) => {
                    if (a.id === "other") return 1;
                    if (b.id === "other") return -1;
                    return b.totalCount - a.totalCount;
                });
            }

            tabs.push({
                prefix,
                name: PREFIX_NAMES[prefix] || prefix,
                termIds: group.termIds,
                totalCount: group.totalCount,
                tree,
                branches,
            });
        }

        // Sort tabs by total count, OTHER last
        tabs.sort((a, b) => {
            if (a.prefix === 'OTHER') return 1;
            if (b.prefix === 'OTHER') return -1;
            return b.totalCount - a.totalCount;
        });

        return tabs;
    }, [termsByPrefix, ontologyTrees, cvTermCounts]);

    // Set default active tab
    useEffect(() => {
        if (ontologyTabs.length > 0 && !activeTab) {
            setActiveTab(ontologyTabs[0].prefix);
        }
    }, [ontologyTabs, activeTab]);

    // Reset active tab when entities change
    useEffect(() => {
        setActiveTab("");
    }, [selectedEntities]);

    // Total annotation count
    const totalAnnotations = useMemo(() =>
        Array.from(cvTermCounts.values()).reduce((sum, count) => sum + count, 0),
        [cvTermCounts]
    );

    // Clear sidebar
    useEffect(() => {
        setSidebarContent(null);
        return () => setSidebarContent(null);
    }, [setSidebarContent]);

    if (selectedEntities.length === 0) {
        return (
            <div className="flex-1 flex flex-col">
                <div className="flex-1 flex items-center justify-center">
                    <p className="text-muted-foreground">Select entities to see their CV term annotations</p>
                </div>
            </div>
        );
    }

    if (loading) {
        return (
            <div className="flex-1 flex flex-col">
                <div className="flex-1 flex items-center justify-center">
                    <div className="animate-pulse text-muted-foreground">Loading annotations...</div>
                </div>
            </div>
        );
    }

    if (cvTermCounts.size === 0) {
        return (
            <div className="flex-1 flex flex-col">
                <div className="flex-1 flex items-center justify-center">
                    <p className="text-muted-foreground">No CV term annotations found for the selected entities</p>
                </div>
            </div>
        );
    }

    return (
        <div className="flex-1 flex flex-col">
            <div className="flex-1 overflow-y-auto">
                <div className="w-full max-w-screen-xl mx-auto px-4 py-6">
                    <div className="text-sm text-muted-foreground mb-4">
                        Found {formatNumber(cvTermCounts.size)} CV terms with {formatNumber(totalAnnotations)} total occurrences across {selectedEntities.length} entit{selectedEntities.length === 1 ? 'y' : 'ies'}
                    </div>

                    {ontologyTabs.length > 0 && (
                        <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
                            {/* Ontology roots as tabs */}
                            <TabsList className="flex flex-wrap h-auto gap-1 mb-6 bg-muted/50 p-1">
                                {ontologyTabs.map((tab) => (
                                    <TabsTrigger
                                        key={tab.prefix}
                                        value={tab.prefix}
                                        className={cn(
                                            "flex items-center gap-2 px-4 py-2 text-sm",
                                            "data-[state=active]:bg-background data-[state=active]:shadow-sm"
                                        )}
                                    >
                                        <span>{tab.name}</span>
                                        <Badge variant="secondary" className="ml-1 text-xs">
                                            {formatNumber(tab.termIds.length)}
                                        </Badge>
                                    </TabsTrigger>
                                ))}
                            </TabsList>

                            {/* Tab content with tree */}
                            {ontologyTabs.map((tab) => (
                                <TabsContent key={tab.prefix} value={tab.prefix} className="mt-0">
                                    <div className="border rounded-lg bg-card overflow-hidden">
                                        {tab.branches.length > 0 ? (
                                            <Accordion
                                                type="multiple"
                                                defaultValue={tab.branches.map(b => b.id)}
                                                className="w-full"
                                            >
                                                {tab.branches.map((branch) => (
                                                    <AccordionItem key={branch.id} value={branch.id} className="border-b last:border-b-0">
                                                        <AccordionTrigger className="py-3 px-4 hover:bg-muted/50 hover:no-underline text-sm font-medium">
                                                            <div className="flex items-center gap-2">
                                                                <span>{branch.name}</span>
                                                                <Badge variant="outline" className="text-xs">
                                                                    {formatNumber(branch.totalCount)}
                                                                </Badge>
                                                            </div>
                                                        </AccordionTrigger>
                                                        <AccordionContent className="pb-3 px-4">
                                                            <div className="space-y-3">
                                                                {branch.parents.map((parent) => (
                                                                    <div key={parent.id} className="space-y-1">
                                                                        {parent.id !== branch.id && parent.id !== "other" && (
                                                                            <div className="text-xs font-medium text-muted-foreground pl-2 py-0.5">
                                                                                {parent.name}
                                                                            </div>
                                                                        )}
                                                                        <div className="space-y-0.5 border-l-2 ml-2 pl-3 border-muted">
                                                                            {parent.terms.map((term) => (
                                                                                <div
                                                                                    key={term.termId}
                                                                                    className="flex items-center justify-between py-1.5 px-2 hover:bg-muted/50 rounded-md group"
                                                                                >
                                                                                    <CvTermHoverCard termId={term.termId}>
                                                                                        <span className="text-sm cursor-help hover:underline">
                                                                                            {term.termId}
                                                                                        </span>
                                                                                    </CvTermHoverCard>
                                                                                    <Badge variant="outline" className="text-xs opacity-70 group-hover:opacity-100">
                                                                                        {formatNumber(term.count)}
                                                                                    </Badge>
                                                                                </div>
                                                                            ))}
                                                                        </div>
                                                                    </div>
                                                                ))}
                                                            </div>
                                                        </AccordionContent>
                                                    </AccordionItem>
                                                ))}
                                            </Accordion>
                                        ) : (
                                            // Fallback: flat list when no tree
                                            <div className="divide-y divide-border">
                                                {tab.termIds.map((termId) => (
                                                    <div key={termId} className="flex items-center justify-between py-3 px-4 hover:bg-muted/50">
                                                        <CvTermHoverCard termId={termId}>
                                                            <span className="text-sm cursor-help hover:underline">{termId}</span>
                                                        </CvTermHoverCard>
                                                        <Badge variant="outline" className="text-xs">
                                                            {formatNumber(cvTermCounts.get(termId) || 0)}
                                                        </Badge>
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                </TabsContent>
                            ))}
                        </Tabs>
                    )}
                </div>
            </div>
        </div>
    );
}
