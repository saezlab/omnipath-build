"use client"

import { createContext, useContext, ReactNode, useState, useCallback } from "react"
import type { SearchResult } from "@/features/search/components/result-card"

export interface SelectedEntity {
  id: string
  entityId?: number
  name: string
  type?: string
  // Related entity IDs for explore tabs
  complexes?: number[]
  cv_terms?: number[]
  pathways?: number[]
  reactions?: number[]
  references?: string[]
  // Store full search result for proper display
  fullResult?: SearchResult
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

  const addEntity = useCallback((entity: SelectedEntity) => {
    setSelectedEntities(prev => {
      if (prev.some(e => e.id === entity.id)) {
        return prev
      }
      return [...prev, entity]
    })
  }, [])

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
