"use client"
import React from "react";
import { ResultCard, type SearchResult } from "./result-card";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

interface SearchResultsProps {
  results: Array<SearchResult>;
  loading?: boolean;
  loadingMore?: boolean;
  hasMore?: boolean;
  sentinelRef?: React.RefObject<HTMLElement | null>;
}

export function SearchResults({
  results,
  loading = false,
  loadingMore = false,
  hasMore = false,
  sentinelRef
}: SearchResultsProps) {

  // Collect all IDs that need name resolution
  const idsToFetch = React.useMemo(() => {
    const ids = new Set<string>();
    results.forEach(result => {
      // Add reactants
      if (result.reactants) {
        result.reactants.forEach(id => ids.add(String(id)));
      }
      // Add products
      if (result.products) {
        result.products.forEach(id => ids.add(String(id)));
      }
      // Add pathway steps
      if (result.pathway_steps) {
        result.pathway_steps.forEach(step => {
          if (!step) return;
          const parts = step.split(':');
          if (parts.length > 1) ids.add(parts[1]);
        });
      }
    });
    return Array.from(ids);
  }, [results]);

  const { data: entityNames = {} } = useQuery({
    queryKey: ['entity-names', idsToFetch],
    queryFn: async () => {
      if (idsToFetch.length === 0) return {};

      const res = await fetch('/api/entity-names', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ ids: idsToFetch }),
      });

      if (!res.ok) {
        throw new Error('Failed to fetch entity names');
      }

      return res.json() as Promise<Record<string, string>>;
    },
    enabled: idsToFetch.length > 0,
    staleTime: 5 * 60 * 1000, // Cache for 5 minutes
  });

  if (loading && !results.length) {
    return null;
  }

  if (!results.length && !loading) {
    return (
      <div className="w-full max-w-screen-xl mx-auto px-4 sm:px-6 lg:px-8 pt-24 text-center">
        <svg className="w-16 h-16 text-slate-400 mx-auto mb-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />
        </svg>
        <p className="text-xl text-slate-500">No results found for your search.</p>
        <p className="text-slate-400">Try refining your search terms.</p>
      </div>
    );
  }

  return (
    <div className="w-full">
      <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }} id="resultsGrid">
        {results.map((result, i) => {
          const resultType = result.type ?? 'entity';
          let href: string | undefined;

          if (resultType === 'cv_term') {
            href = `/cv_term/${result.id}`;
          } else if (resultType === 'entity') {
            href = undefined;
          } else {
            // Fallback to explore with entity filter
            const entityId = result.entity_id ?? result.id;
            href = entityId ? `/explore?entity=${entityId}` : `/explore`;
          }

          const key = (result.entity_id || result.id || i)?.toString();

          if (!href) {
            return (
              <div key={key}>
                <ResultCard result={result} entityNamesMap={entityNames} />
              </div>
            );
          }

          return (
            <Link key={key} href={href}>
              <ResultCard result={result} entityNamesMap={entityNames} />
            </Link>
          );
        })}
      </div>

      {/* Infinite scroll sentinel */}
      {sentinelRef && (
        <div
          ref={sentinelRef as React.RefObject<HTMLDivElement>}
          className="flex justify-center py-8"
          style={{ visibility: hasMore ? 'visible' : 'hidden', height: hasMore ? 'auto' : '0' }}
        >
          {loadingMore ? (
            <div className="flex items-center gap-2">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
              <span className="text-sm text-muted-foreground">Loading more...</span>
            </div>
          ) : (
            <div className="h-4" />
          )}
        </div>
      )}

    </div>
  );
} 
