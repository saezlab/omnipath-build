"use client"

import { useEffect, useState } from "react"
import { searchMeilisearch } from "@/lib/meilisearch/search"

interface EntityData {
  id: string
  canonical_identifier: string
  gene_symbol?: string
  description?: string
  entity_type_name?: string
  ncbi_tax_name?: string
  all_identifiers?: string[]
  interaction_ids?: string[]
  synonyms?: string[]
}

interface UseEntityResult {
  data: EntityData | null
  loading: boolean
  error: Error | null
}

export function useEntity(entityId: string | undefined): UseEntityResult {
  const [data, setData] = useState<EntityData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    if (!entityId) {
      setData(null)
      return
    }

    let cancelled = false

    const fetchEntity = async () => {
      setLoading(true)
      setError(null)

      try {
        // Search for entity by canonical identifier
        const result = await searchMeilisearch({
          index: "entities",
          query: entityId,
          limit: 1,
          offset: 0,
      })
        
        if (!cancelled) {
          const hits = result.hits as unknown as EntityData[] || [];
          if (hits.length > 0) {
            setData(hits[0])
          } else {
            setData(null)
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err : new Error("Unknown error"))
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    fetchEntity()

    return () => {
      cancelled = true
    }
  }, [entityId])

  return { data, loading, error }
}