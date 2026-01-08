import { useCallback, useEffect, useRef, useState } from 'react'

interface UseInfiniteScrollOptions<T> {
  fetchData: (offset: number, limit: number) => Promise<{
    results: T[]
    totalResults: number
  }>
  pageSize?: number
  rootMargin?: string
  threshold?: number
  dependencies?: unknown[]
  root?: HTMLElement | null
}

interface UseInfiniteScrollReturn<T> {
  data: T[]
  loading: boolean
  loadingMore: boolean
  hasMore: boolean
  error: Error | null
  totalResults: number
  sentinelRef: React.RefObject<HTMLElement | null>
  refetch: () => Promise<void>
  reset: () => void
}

export function useInfiniteScroll<T>({
  fetchData,
  pageSize = 50,
  rootMargin = '100px',
  threshold = 0,
  dependencies = [],
  root = null
}: UseInfiniteScrollOptions<T>): UseInfiniteScrollReturn<T> {
  const [data, setData] = useState<T[]>([])
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [totalResults, setTotalResults] = useState(0)

  const sentinelRef = useRef<HTMLElement | null>(null)
  const observerRef = useRef<IntersectionObserver | null>(null)

  // Use refs to avoid stale closures in observer callback
  const loadingMoreRef = useRef(loadingMore)
  const hasMoreRef = useRef(hasMore)
  const dataRef = useRef(data)

  // Update refs when state changes
  useEffect(() => {
    loadingMoreRef.current = loadingMore
  }, [loadingMore])

  useEffect(() => {
    hasMoreRef.current = hasMore
  }, [hasMore])

  useEffect(() => {
    dataRef.current = data
  }, [data])

  const loadMore = useCallback(async () => {
    if (loadingMoreRef.current || !hasMoreRef.current) {
      return
    }

    const offset = dataRef.current.length
    setLoadingMore(true)
    setError(null)

    try {
      const response = await fetchData(offset, pageSize)

      setData(prev => [...prev, ...response.results])
      setTotalResults(response.totalResults)
      
      // Check if we received a full page of results
      const newHasMore = response.results.length === pageSize && offset + response.results.length < response.totalResults
      setHasMore(newHasMore)
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Failed to load more data'))
    } finally {
      setLoadingMore(false)
    }
  }, [fetchData, pageSize])

  const refetch = useCallback(async () => {
    setLoading(true)
    setError(null)
    setData([])
    setHasMore(true)

    try {
      const response = await fetchData(0, pageSize)
      
      setData(response.results)
      setTotalResults(response.totalResults)
      const initialHasMore = response.results.length === pageSize && response.results.length < response.totalResults
      setHasMore(initialHasMore)
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Failed to fetch data'))
    } finally {
      setLoading(false)
    }
  }, [fetchData, pageSize])

  const reset = useCallback(() => {
    setData([])
    setLoading(false)
    setLoadingMore(false)
    setHasMore(true)
    setError(null)
    setTotalResults(0)
  }, [])

  // Set up intersection observer
  useEffect(() => {
    // Use an interval to check for sentinel element
    const checkInterval = setInterval(() => {
      if (sentinelRef.current && !observerRef.current) {
        
        const observerCallback = (entries: IntersectionObserverEntry[]) => {
          const [entry] = entries

          if (entry.isIntersecting && hasMoreRef.current && !loadingMoreRef.current) {
            loadMore()
          }
        }

        // Create observer with root
        observerRef.current = new IntersectionObserver(observerCallback, {
          root,
          rootMargin,
          threshold
        })

        observerRef.current.observe(sentinelRef.current)
        
        // Clear the interval once observer is set up
        clearInterval(checkInterval)
      }
    }, 100)

    return () => {
      clearInterval(checkInterval)
      if (observerRef.current) {
        observerRef.current.disconnect()
        observerRef.current = null
      }
    }
  // Re-run when these change
  }, [loadMore, rootMargin, threshold, root])

  // Store previous dependencies to detect changes
  const prevDependenciesRef = useRef<unknown[]>([])
  
  // Initial fetch and reset on dependency changes
  useEffect(() => {
    // Check if dependencies have actually changed
    const depsChanged = 
      dependencies.length !== prevDependenciesRef.current.length ||
      dependencies.some((dep, i) => dep !== prevDependenciesRef.current[i])
    
    if (depsChanged) {
      prevDependenciesRef.current = dependencies
      refetch()
    }
  }, [refetch, dependencies])

  return {
    data,
    loading,
    loadingMore,
    hasMore,
    error,
    totalResults,
    sentinelRef,
    refetch,
    reset
  }
}