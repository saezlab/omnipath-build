"use client"

import { ChevronRight, Home } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useEffect, useState } from "react";
import { fetchCvTermsByIds } from "@/lib/meilisearch/search";

interface CvTermData {
  id: string;
  name: string;
  namespace?: string;
  directParentIds?: string[];
  categoryId?: string;
  [key: string]: unknown;
}

interface OntologyHierarchyBreadcrumbProps {
  currentTerm: CvTermData;
  onNodeClick?: (termId: string) => void;
}

export function OntologyHierarchyBreadcrumb({ 
  currentTerm, 
  onNodeClick 
}: OntologyHierarchyBreadcrumbProps) {
  const [categoryTerm, setCategoryTerm] = useState<CvTermData | null>(null);
  const [parentTerms, setParentTerms] = useState<CvTermData[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadRelatedTerms() {
      try {
        // Reset state
        setCategoryTerm(null);
        setParentTerms([]);
        
        console.log('Current term in breadcrumb:', currentTerm);
        console.log('Category ID:', currentTerm.categoryId);
        console.log('Direct parent IDs:', currentTerm.directParentIds);
        
        // If we don't have the enhanced fields, try to re-fetch the current term
        // to get the latest data with directParentIds and categoryId
        if (!currentTerm.directParentIds && !currentTerm.categoryId) {
          console.log('Missing enhanced fields, re-fetching current term...');
          const { documents: currentTermDocs } = await fetchCvTermsByIds([currentTerm.id]);
          const enhancedTerm = currentTermDocs[0] as CvTermData;
          console.log('Enhanced term data:', enhancedTerm);
          
          if (enhancedTerm) {
            // Note: We should update the component state instead of reassigning parameters
            // This is a React hooks exhaustive-deps warning
          }
        }
        
        // Collect all term IDs we need to fetch
        const termIdsToFetch: string[] = [];
        
        // Add category ID if exists
        if (currentTerm.categoryId) {
          termIdsToFetch.push(currentTerm.categoryId);
        }
        
        // Add direct parent IDs
        if (currentTerm.directParentIds && currentTerm.directParentIds.length > 0) {
          termIdsToFetch.push(...currentTerm.directParentIds);
        }
        
        console.log('Term IDs to fetch:', termIdsToFetch);
        
        // Fetch category and direct parents
        if (termIdsToFetch.length > 0) {
          const { documents: relatedTerms } = await fetchCvTermsByIds(termIdsToFetch);
          console.log('Fetched related terms:', relatedTerms);
          
          // Process fetched terms
          const newParentTerms: CvTermData[] = [];
          
          relatedTerms.forEach((relatedTerm: Record<string, unknown>) => {
            const typedTerm = relatedTerm as CvTermData;
            // Set category term
            if (currentTerm.categoryId && typedTerm.id === currentTerm.categoryId) {
              setCategoryTerm(typedTerm);
            }
            
            // Collect parent terms
            if (currentTerm.directParentIds?.includes(typedTerm.id)) {
              newParentTerms.push(typedTerm);
            }
          });
          
          setParentTerms(newParentTerms);
        } else {
          console.log('No related terms to fetch');
        }
      } catch (error) {
        console.error('Error loading related terms:', error);
      } finally {
        setLoading(false);
      }
    }
    
    setLoading(true);
    loadRelatedTerms();
  }, [currentTerm]);

  const handleNodeClick = (termId: string) => {
    if (onNodeClick) {
      onNodeClick(termId);
    }
  };
  
  if (loading || !currentTerm) {
    return <div className="bg-muted/30 dark:bg-muted/50 rounded-lg p-3 mb-4 h-12 animate-pulse" />;
  }

  return (
    <div className="bg-muted/30 dark:bg-muted/50 rounded-lg p-3 mb-4">
      <div className="flex items-center flex-wrap gap-1 text-sm">
        {/* Root (namespace) with home icon */}
        <div className="flex items-center gap-1 text-muted-foreground">
          <Home className="h-3 w-3" />
          <span className="text-sm">{currentTerm.namespace || 'Ontology'}</span>
        </div>
        
        <ChevronRight className="h-3 w-3 text-muted-foreground/60" />
        
        {/* Main branch (category) */}
        {categoryTerm && (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => handleNodeClick(categoryTerm.id)}
              className="h-auto p-1 text-muted-foreground hover:text-foreground hover:bg-background/80 rounded-md"
            >
              {categoryTerm.name}
            </Button>
          </>
        )}
        
        {/* Show ellipsis if there are intermediate levels */}
        {parentTerms.length > 0 && (
          <>
            <ChevronRight className="h-3 w-3 text-muted-foreground/60" />
            <span className="text-muted-foreground/80 px-1">…</span>
          </>
        )}
        
        {/* Direct parent(s) */}
        {parentTerms.map((parent) => (
          <div key={parent.id} className="flex items-center">
            <ChevronRight className="h-3 w-3 text-muted-foreground/60" />
            <Button
              variant="ghost"
              size="sm"
              onClick={() => handleNodeClick(parent.id)}
              className="h-auto p-1 text-muted-foreground hover:text-foreground hover:bg-background/80 rounded-md"
            >
              {parent.name}
            </Button>
          </div>
        ))}
        
        {/* Current term */}
        <ChevronRight className="h-3 w-3 text-muted-foreground/60" />
        <div className="flex items-center gap-2 bg-background/60 dark:bg-background/80 rounded-md px-2 py-1 border border-border/50">
          <span className="font-semibold text-foreground text-sm">{currentTerm.name}</span>
          <Badge variant="secondary" className="text-xs">
            {currentTerm.namespace}
          </Badge>
        </div>
      </div>
    </div>
  );
}