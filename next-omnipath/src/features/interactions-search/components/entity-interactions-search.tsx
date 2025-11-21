"use client"

import { InteractionsSearch } from "./interactions-search"

interface EntityInteractionsSearchProps {
  entityId: number
  entityName?: string
}

/**
 * Component for viewing interactions for a specific entity.
 * Note: With the new schema, filtering by entity is done via member_a_id/member_b_id filters.
 * This component would need additional logic to pre-filter by the entity.
 */
export function EntityInteractionsSearch({
  entityId,
  entityName = ""
}: EntityInteractionsSearchProps) {
  // TODO: Pass entityId as a pre-filter to InteractionsSearch
  // The new schema uses member_a_id and member_b_id for filtering
  console.log(`Entity interactions for: ${entityId} (${entityName})`)

  return (
    <InteractionsSearch
      hideFilters={false}
    />
  )
}
