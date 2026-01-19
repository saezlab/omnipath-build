"use client";

import { useState, useMemo, useEffect } from "react";
import { useEntitySelection } from "@/contexts/entity-selection-context";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { CvTermHoverCard } from "@/features/search/components/result-card";
import { formatNumber } from "@/lib/utils";

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
}

export function AnnotationsExploreTab() {
    const { selectedEntities } = useEntitySelection();
    const [annotationTree, setAnnotationTree] = useState<TreeNode | null>(null);
    const [loading, setLoading] = useState(false);

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

    // Get unique CV term IDs
    const cvTermIds = useMemo(() => Array.from(cvTermCounts.keys()), [cvTermCounts]);

    // Fetch ontology tree when term IDs change
    useEffect(() => {
        if (cvTermIds.length === 0) {
            setAnnotationTree(null);
            return;
        }

        setLoading(true);
        const loadTree = async () => {
            try {
                const response = await fetch("/api/ontology/tree", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        termIds: cvTermIds
                    }),
                });

                if (!response.ok) {
                    throw new Error(`Failed to load hierarchy (${response.status})`);
                }

                const data = (await response.json()) as { root?: TreeNode | null };
                setAnnotationTree(data.root || null);
            } catch (error) {
                console.error("Error loading ontology tree:", error);
            } finally {
                setLoading(false);
            }
        };

        loadTree();
    }, [cvTermIds]);

    // Build annotation groups from tree
    const annotationGroups = useMemo(() => {
        if (!annotationTree || cvTermCounts.size === 0) return null;

        const parentById = new Map<string, TreeNode>();
        const rootChildById = new Map<string, TreeNode>();
        const nameById = new Map<string, string>();

        const visit = (node: TreeNode, parent: TreeNode | null, rootChild: TreeNode | null) => {
            nameById.set(node.id, node.name || node.id);
            if (parent) {
                parentById.set(node.id, parent);
            }
            if (rootChild) {
                rootChildById.set(node.id, rootChild);
            }
            node.children?.forEach((child) => {
                const nextRootChild = rootChild ?? child;
                visit(child, node, nextRootChild);
            });
        };

        visit(annotationTree, null, null);

        const branchMap = new Map<string, AnnotationBranchGroup>();
        const ensureBranch = (id: string, name: string) => {
            if (!branchMap.has(id)) {
                branchMap.set(id, { id, name, parents: [] });
            }
            return branchMap.get(id)!;
        };

        const ensureParent = (branch: AnnotationBranchGroup, id: string, name: string) => {
            const existing = branch.parents.find((parent) => parent.id === id);
            if (existing) return existing;
            const parentGroup = { id, name, terms: [] as { termId: string; count: number }[] };
            branch.parents.push(parentGroup);
            return parentGroup;
        };

        for (const [termId, count] of cvTermCounts.entries()) {
            const parent = parentById.get(termId);
            const rootChild = rootChildById.get(termId);
            const branch = rootChild
                ? ensureBranch(rootChild.id, nameById.get(rootChild.id) || rootChild.id)
                : ensureBranch("other", "Other");

            if (parent) {
                ensureParent(branch, parent.id, nameById.get(parent.id) || parent.id).terms.push({ termId, count });
            } else {
                ensureParent(branch, "other", "Other").terms.push({ termId, count });
            }
        }

        const branches = Array.from(branchMap.values());
        const parentCount = (group: AnnotationParentGroup) =>
            group.terms.reduce((sum, term) => sum + term.count, 0);
        const branchCount = (branch: AnnotationBranchGroup) =>
            branch.parents.reduce((sum, parent) => sum + parentCount(parent), 0);

        branches.forEach((branch) => {
            branch.parents.forEach((parent) => {
                parent.terms.sort((a, b) => b.count - a.count);
            });
            branch.parents.sort((a, b) => parentCount(b) - parentCount(a));
        });

        branches.sort((a, b) => {
            if (a.id === "other") return 1;
            if (b.id === "other") return -1;
            return branchCount(b) - branchCount(a);
        });

        return {
            rootName: annotationTree.name || annotationTree.id,
            branches
        };
    }, [annotationTree, cvTermCounts]);

    // Total annotation count
    const totalAnnotations = useMemo(() =>
        Array.from(cvTermCounts.values()).reduce((sum, count) => sum + count, 0),
        [cvTermCounts]
    );

    // Show empty state when no entities selected
    if (selectedEntities.length === 0) {
        return (
            <div className="flex items-center justify-center py-12">
                <p className="text-muted-foreground">
                    Select entities to see annotations and CV terms
                </p>
            </div>
        );
    }

    // Show loading state
    if (loading) {
        return (
            <div className="flex items-center justify-center py-12">
                <div className="animate-pulse text-muted-foreground">
                    Loading annotations...
                </div>
            </div>
        );
    }

    // Show empty state when no CV terms found
    if (cvTermCounts.size === 0) {
        return (
            <div className="flex items-center justify-center py-12">
                <p className="text-muted-foreground">
                    No CV terms found for the selected entities
                </p>
            </div>
        );
    }

    return (
        <div className="space-y-4">
            {annotationGroups ? (
                <Accordion type="multiple" defaultValue={annotationGroups.branches.map(b => b.id)} className="w-full space-y-2">
                    <div className="text-lg font-semibold mb-4">
                        {annotationGroups.rootName}
                    </div>
                    {annotationGroups.branches.map((branch) => (
                        <AccordionItem key={branch.id} value={branch.id} className="border rounded-lg px-4">
                            <AccordionTrigger className="py-3 hover:no-underline text-base font-medium">
                                {branch.name}
                            </AccordionTrigger>
                            <AccordionContent className="pb-4">
                                <div className="space-y-4 pl-2">
                                    {branch.parents.map((parent) => (
                                        <div key={parent.id} className="space-y-2">
                                            {parent.id !== branch.id && (
                                                <div className="text-sm font-medium text-muted-foreground pl-2 py-1">
                                                    {parent.name}
                                                </div>
                                            )}
                                            <div className="space-y-1 border-l-2 ml-2 pl-3 border-muted">
                                                {parent.terms.map((term) => (
                                                    <div key={term.termId} className="flex items-center justify-between py-1 hover:bg-muted/50 rounded px-2 -ml-2">
                                                        <CvTermHoverCard termId={term.termId}>
                                                            <span className="text-sm cursor-help hover:underline">
                                                                {term.termId}
                                                            </span>
                                                        </CvTermHoverCard>
                                                        <Badge variant="outline" className="text-xs">
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
                // Fallback: show flat list when tree is not available
                <div className="space-y-2">
                    {Array.from(cvTermCounts.entries())
                        .sort((a, b) => b[1] - a[1])
                        .map(([termId, count]) => (
                            <div key={termId} className="flex items-center justify-between py-2 px-3 border rounded-lg hover:bg-muted/50">
                                <CvTermHoverCard termId={termId}>
                                    <span className="text-sm cursor-help hover:underline">
                                        {termId}
                                    </span>
                                </CvTermHoverCard>
                                <Badge variant="outline" className="text-xs">
                                    {formatNumber(count)}
                                </Badge>
                            </div>
                        ))}
                </div>
            )}
        </div>
    );
}
