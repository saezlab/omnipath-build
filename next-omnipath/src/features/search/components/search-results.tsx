"use client"
import React from "react";
import { ResultCard, type SearchResult } from "./result-card";
import Link from "next/link";
import { Skeleton } from "@/components/ui/skeleton";

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

  if (loading && !results.length) {
    return (
      <div className="w-full">
        <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
          {[...Array(8)].map((_, i) => (
            <div key={i} className="p-4 border rounded-lg space-y-3">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-3 w-1/2" />
              <Skeleton className="h-20 w-full" />
              <div className="flex gap-2">
                <Skeleton className="h-6 w-16" />
                <Skeleton className="h-6 w-16" />
              </div>
            </div>
          ))}
        </div>
      </div>
    );
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
          let href: string;

          if (result.type === 'cv_term') {
            href = `/cv_term/${result.id}`;
          } else if (result.type === 'entity') {
            // Navigate to explore page with entity filter applied
            const entityId = result.entity_id ?? result.id;
            if (entityId) {
              href = `/explore?entity=${entityId}`;
            } else {
              href = `/explore`;
            }
          } else {
            // Fallback to explore with entity filter
            const entityId = result.entity_id ?? result.id;
            href = entityId ? `/explore?entity=${entityId}` : `/explore`;
          }

          const key = (result.entity_id || result.id || i)?.toString();

          return (
            <Link key={key} href={href}>
              <ResultCard result={result} />
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
      
      {/* End of results message */}
      {sentinelRef && !hasMore && results.length > 0 && (
        <div className="py-4 text-center text-sm text-muted-foreground">
          No more results to load
        </div>
      )}
    </div>
  );
} 