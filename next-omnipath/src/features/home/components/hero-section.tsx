"use client"

import { useState, useEffect } from "react"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { getNetworkMetrics, type NetworkMetrics } from "../api/queries"

export function HeroSection() {
  const [metrics, setMetrics] = useState<NetworkMetrics | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        const data = await getNetworkMetrics()
        setMetrics(data)
      } catch (error) {
        console.error('Failed to fetch network metrics:', error)
      } finally {
        setLoading(false)
      }
    }

    fetchMetrics()
  }, [])

  const formatNumber = (num: number) => {
    if (num >= 1000000) {
      return `${(num / 1000000).toFixed(1)}M`
    } else if (num >= 1000) {
      return `${(num / 1000).toFixed(0)}K`
    }
    return num.toLocaleString()
  }

  return (
    <div className="relative overflow-hidden">
      <div className="container relative mx-auto py-32 px-4 text-center">
        <h1 className="text-5xl md:text-6xl font-medium tracking-tight mb-6 text-foreground">
          Molecular data
          <span className="block text-muted-foreground">at scale</span>
        </h1>
        <p className="text-lg text-muted-foreground max-w-xl mx-auto mb-8 leading-relaxed">
          Unified access to molecular interactions, pathways, and annotations from 100+ scientific databases.
        </p>
        
        {/* Dynamic metrics display */}
        {!loading && metrics && (
          <div className="flex gap-8 justify-center mb-8 text-sm">
            <div className="text-center">
              <div className="text-2xl font-bold text-foreground">{formatNumber(metrics.total_entities)}</div>
              <div className="text-muted-foreground">Entities</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-foreground">{formatNumber(metrics.total_interactions)}</div>
              <div className="text-muted-foreground">Interactions</div>
            </div>
            {/* need to fix */}
            {/* <div className="text-center">
              <div className="text-2xl font-bold text-foreground">{metrics.total_data_sources}</div>
              <div className="text-muted-foreground">Data Sources</div>
            </div> */}
            <div className="text-center">
              <div className="text-2xl font-bold text-foreground">{formatNumber(metrics.total_references)}</div>
              <div className="text-muted-foreground">References</div>
            </div>
          </div>
        )}
        
        <div className="flex gap-3 justify-center">
          <Button size="lg" variant="default" asChild>
            <Link href="/search">Get started</Link>
          </Button>
          <Button size="lg" variant="outline" asChild>
            <Link href="/interactions/search">Browse data</Link>
          </Button>
        </div>
      </div>
    </div>
  )
}

