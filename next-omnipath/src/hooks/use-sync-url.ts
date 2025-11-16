import { useSearchParams, useRouter } from 'next/navigation'
import { useEffect } from 'react'
import { useSearchStore } from '@/store/search-store'

// Note: This hook is deprecated. The new InteractionsSearch component handles URL sync internally.
// Keeping for backward compatibility during migration.
export function useSyncUrl() {
  const searchParams = useSearchParams()
  const router = useRouter()
  const {
    interactionsQuery,
    setInteractionsQuery,
  } = useSearchStore()

  // Sync URL with store state
  useEffect(() => {
    const params = new URLSearchParams(searchParams.toString())
    
    // Update store from URL
    const urlInteractionsQuery = params.get('interactions')
    
    if (urlInteractionsQuery && urlInteractionsQuery !== interactionsQuery) {
      setInteractionsQuery(urlInteractionsQuery)
      // Note: Search is now handled by the InteractionsSearch component
    }
  }, [searchParams, interactionsQuery, setInteractionsQuery])

  // Update URL when store changes
  useEffect(() => {
    const params = new URLSearchParams(searchParams.toString())
    
    if (interactionsQuery) {
      params.set('interactions', interactionsQuery)
    } else {
      params.delete('interactions')
    }
    
    // Only update URL if there are actual changes
    if (params.toString() !== searchParams.toString()) {
      router.push(`?${params.toString()}`, { scroll: false })
    }
  }, [interactionsQuery, router, searchParams])
} 