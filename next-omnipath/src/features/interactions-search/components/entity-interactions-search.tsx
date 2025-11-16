"use client"

import { InteractionsSearch } from "./interactions-search"

interface EntityInteractionsSearchProps {
  entityId: string
  entityName?: string
}

export function EntityInteractionsSearch({ 
  entityId,
  entityName = ""
}: EntityInteractionsSearchProps) {
  return (
    <InteractionsSearch
      entityId={entityId}
      entityName={entityName}
      hideEntityFilter={true}
    />
  )
}