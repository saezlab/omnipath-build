"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { AlertCircle, Loader2 } from "lucide-react"
import { InteractionDetails } from "@/features/interactions-search/components/interaction-details"
import { getInteractionEvidences, PaginatedEvidenceResponse } from "../api/queries"
import { InteractionEvidenceDetail } from "../api/queries"
import { MeilisearchInteraction } from "@/types/meilisearch"

interface InteractionDetailsSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  interaction: MeilisearchInteraction | null
}

export function InteractionDetailsSheet({ open, onOpenChange, interaction }: InteractionDetailsSheetProps) {
  const [evidences, setEvidences] = useState<InteractionEvidenceDetail[]>([])
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [currentPage, setCurrentPage] = useState(1)
  const [hasNextPage, setHasNextPage] = useState(false)
  const [hasLoadedInitial, setHasLoadedInitial] = useState(false)
  
  const observerRef = useRef<IntersectionObserver | null>(null)
  const loadMoreRef = useRef<HTMLDivElement | null>(null)

  const loadEvidences = useCallback(async (page: number = 1) => {
    if (!interaction) return

    const isLoadingMore = page > 1
    if (isLoadingMore) {
      setLoadingMore(true)
    } else {
      setLoading(true)
    }
    setError(null)

    try {
      const response: PaginatedEvidenceResponse = await getInteractionEvidences(
        parseInt(interaction.id),
        page
      )

      if (isLoadingMore) {
        setEvidences(prev => [...(prev || []), ...(response.data || [])])
      } else {
        setEvidences(response.data || [])
        setHasLoadedInitial(true)
      }

      setCurrentPage(page)
      setHasNextPage(response.hasNext)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load evidence details")
    } finally {
      setLoading(false)
      setLoadingMore(false)
    }
  }, [interaction])

  // Reset state when interaction changes
  useEffect(() => {
    if (interaction) {
      setEvidences([])
      setCurrentPage(1)
      setHasNextPage(false)
      setHasLoadedInitial(false)
      setError(null)
    }
  }, [interaction?.id, interaction])

  // Load initial evidences when sheet opens
  useEffect(() => {
    if (open && interaction && !hasLoadedInitial) {
      loadEvidences(1)
    }
  }, [open, interaction, hasLoadedInitial, loadEvidences])

  // Set up intersection observer for infinite scroll
  useEffect(() => {
    const callback = (entries: IntersectionObserverEntry[]) => {
      if (entries[0].isIntersecting && hasNextPage && !loadingMore && !loading) {
        loadEvidences(currentPage + 1)
      }
    }

    const observer = new IntersectionObserver(callback, {
      rootMargin: '100px',
      threshold: 0.1
    })
    
    observerRef.current = observer

    const element = loadMoreRef.current
    if (element && open) {
      observer.observe(element)
    }

    return () => {
      if (element) {
        observer.unobserve(element)
      }
      observer.disconnect()
    }
  }, [hasNextPage, loadingMore, loading, open, loadEvidences, currentPage])

  // Create interaction object compatible with InteractionDetails component
  // Apply the same entity swapping logic as in the table to maintain consistency
  const shouldSwap = interaction && interaction.is_directed && interaction.consensus_direction === 'reverse';
  
  const detailsInteraction = interaction ? {
    id: interaction.id,
    entity_a: {
      id: shouldSwap ? interaction.entity_b_canonical_id : interaction.entity_a_canonical_id,
      canonical_identifier: shouldSwap ? interaction.entity_b_canonical_id : interaction.entity_a_canonical_id,
      display_name: shouldSwap ? interaction.entity_b_name : interaction.entity_a_name,
      entity_type: { name: 'protein' } // Default, as we don't have this info
    },
    entity_b: {
      id: shouldSwap ? interaction.entity_a_canonical_id : interaction.entity_b_canonical_id,
      canonical_identifier: shouldSwap ? interaction.entity_a_canonical_id : interaction.entity_b_canonical_id,
      display_name: shouldSwap ? interaction.entity_a_name : interaction.entity_b_name,
      entity_type: { name: 'protein' } // Default, as we don't have this info
    },
    consensus_sign: interaction.consensus_sign,
    has_directed_evidence: interaction.is_directed,
    evidences: evidences.map(ev => ({
      id: ev.id,
      sign: ev.sign,
      is_directed: ev.isDirected || undefined,
      direction: ev.direction || undefined,
      evidence_sentence: ev.evidenceSentence || undefined,
      data_source: ev.dataSourceName ? {
        name: ev.dataSourceName
      } : undefined,
      interaction_type: ev.interactionTypeName ? {
        id: String(ev.interactionTypeId),
        name: ev.interactionTypeName
      } : undefined,
      causal_statement: ev.causalStatementName ? {
        id: String(ev.causalStatementId),
        name: ev.causalStatementName
      } : undefined,
      causal_mechanism: ev.causalMechanismName ? {
        id: String(ev.causalMechanismId),
        name: ev.causalMechanismName
      } : undefined,
      reference: ev.pubmedId ? {
        pubmed_id: ev.pubmedId
      } : undefined,
    }))
  } : null

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full sm:max-w-2xl overflow-y-auto pb-8">
        <SheetHeader>
          <SheetTitle>Interaction Details</SheetTitle>
          <SheetDescription>
            View detailed evidence for this interaction
          </SheetDescription>
        </SheetHeader>

        {interaction && (
          <div className="mt-6 mb-6 space-y-6">

            {/* Loading State */}
            {loading && (
              <div className="space-y-4">
                <Skeleton className="h-20 w-full" />
                <Skeleton className="h-20 w-full" />
                <Skeleton className="h-20 w-full" />
              </div>
            )}

            {/* Error State */}
            {error && (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            {/* Evidence Details */}
            {!loading && !error && detailsInteraction && (
              <>
                <InteractionDetails selectedInteraction={detailsInteraction} />
                
                {/* Infinite scroll trigger */}
                {hasNextPage && (
                  <div 
                    ref={loadMoreRef}
                    className="flex justify-center py-4"
                  >
                    {loadingMore && (
                      <div className="flex items-center gap-2">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        <span className="text-sm text-muted-foreground">Loading more evidence...</span>
                      </div>
                    )}
                  </div>
                )}
                
                {/* End of results message */}
                {!hasNextPage && evidences.length > 0 && (
                  <div className="py-4 text-center text-sm text-muted-foreground">
                    All evidence loaded
                  </div>
                )}
              </>
            )}

            {/* No Evidence State */}
            {!loading && !error && evidences.length === 0 && (
              <Alert>
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>
                  No evidence details available for this interaction.
                </AlertDescription>
              </Alert>
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}