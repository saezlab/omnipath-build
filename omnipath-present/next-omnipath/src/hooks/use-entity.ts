"use client"


import { useQuery } from "@tanstack/react-query"
import { searchMeilisearch } from "@/lib/meilisearch/search"
import { INDEXES } from "@/lib/meilisearch/client"
import type { SearchResult } from "@/features/search/components/result-card"

interface UseEntityResult {
  data: SearchResult | null
  loading: boolean
  error: Error | null
}

export function useEntity(entityId: string | undefined): UseEntityResult {
  const { data, isLoading, error } = useQuery({
    queryKey: ["entity", entityId],
    queryFn: async () => {
      if (!entityId) return null

      const result = await searchMeilisearch({
        index: INDEXES.ENTITIES,
        query: entityId,
        limit: 1,
        offset: 0,
      })

      const hits = (result.hits as unknown as SearchResult[]) || []
      return hits.length > 0 ? hits[0] : null
    },
    enabled: !!entityId,
  })

  return {
    data: data ?? null,
    loading: isLoading,
    error: error as Error | null
  }
}