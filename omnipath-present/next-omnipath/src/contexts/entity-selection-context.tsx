"use client"

import { createContext, useContext, ReactNode, useState, useCallback } from "react"
import type { SearchResult } from "@/features/search/components/result-card"
import { searchAssociationsMeilisearch } from "@/lib/meilisearch/search"
import { INDEXES } from "@/lib/meilisearch/client"
import type { MeilisearchAssociation } from "@/types/meilisearch"

export interface SelectedEntity {
  id: string
  entityId?: number
  name: string
  type?: string
  // CV terms - now stored as string accessions (e.g., GO:0006915)
  cv_terms?: string[]
  references?: string[]
  // Store full search result for proper display
  fullResult?: SearchResult
  // Associated entity IDs (from complexes, pathways, etc.)
  associated_entity_ids?: number[]
}


interface EntitySelectionContextType {
  selectedEntities: SelectedEntity[]
  addEntity: (entity: SelectedEntity) => void
  removeEntity: (id: string) => void
  clearSelection: () => void
  isSelected: (id: string) => boolean
  selectionCount: number
}

const EntitySelectionContext = createContext<EntitySelectionContextType>({
  selectedEntities: [],
  addEntity: () => { },
  removeEntity: () => { },
  clearSelection: () => { },
  isSelected: () => false,
  selectionCount: 0,
})

export function EntitySelectionProvider({ children }: { children: ReactNode }) {
  const [selectedEntities, setSelectedEntities] = useState<SelectedEntity[]>([])

  const addEntity = useCallback(async (entity: SelectedEntity) => {
    // Check if already selected
    if (selectedEntities.some(e => e.id === entity.id)) {
      return
    }

    // Fetch associated entity IDs if we have a numeric entity ID
    let associated_entity_ids: number[] = []
    if (entity.entityId) {
      try {
        // Query associations in both directions
        const [parentsResponse, membersResponse] = await Promise.all([
          searchAssociationsMeilisearch({
            query: "",
            index: INDEXES.ASSOCIATIONS,
            limit: 10000,
            offset: 0,
            filters: { member_entity_ids: [entity.entityId] }
          }),
          searchAssociationsMeilisearch({
            query: "",
            index: INDEXES.ASSOCIATIONS,
            limit: 10000,
            offset: 0,
            filters: { parent_entity_ids: [entity.entityId] }
          })
        ])

        // Extract unique entity IDs from both queries
        const entityIdSet = new Set<number>()
        const parentHits = parentsResponse.hits as MeilisearchAssociation[]
        const memberHits = membersResponse.hits as MeilisearchAssociation[]

        // Add parent entity IDs
        parentHits.forEach(hit => {
          if (hit.parent_entity_id) entityIdSet.add(hit.parent_entity_id)
        })

        // Add member entity IDs
        memberHits.forEach(hit => {
          if (hit.member_entity_id) entityIdSet.add(hit.member_entity_id)
        })

        associated_entity_ids = Array.from(entityIdSet)
      } catch (error) {
        console.error("Error fetching associations for entity:", entity.id, error)
        // Continue adding entity even if associations fetch fails
      }
    }

    // Add entity with associations
    setSelectedEntities(prev => [...prev, { ...entity, associated_entity_ids }])
  }, [selectedEntities])

  const removeEntity = useCallback((id: string) => {
    setSelectedEntities(prev => prev.filter(e => e.id !== id))
  }, [])

  const clearSelection = useCallback(() => {
    setSelectedEntities([])
  }, [])

  const isSelected = useCallback((id: string) => {
    return selectedEntities.some(e => e.id === id)
  }, [selectedEntities])

  return (
    <EntitySelectionContext.Provider
      value={{
        selectedEntities,
        addEntity,
        removeEntity,
        clearSelection,
        isSelected,
        selectionCount: selectedEntities.length,
      }}
    >
      {children}
    </EntitySelectionContext.Provider>
  )
}

export function useEntitySelection() {
  return useContext(EntitySelectionContext)
}
