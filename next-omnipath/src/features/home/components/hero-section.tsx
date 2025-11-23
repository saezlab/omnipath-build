"use client"

import { useState, useEffect } from "react"
import Link from "next/link"
import { Button } from "@/components/ui/button"

export function HeroSection() {


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
        <h1
          className="text-5xl md:text-6xl font-medium tracking-tight mb-6 text-transparent bg-clip-text"
          style={{ backgroundImage: "linear-gradient(135deg, #007B7F 0%, #4A7C2C 50%, #FCCC06 100%)" }}
        >
          Molecular data
          <span className="block">at scale</span>
        </h1>
        <p className="text-lg text-muted-foreground max-w-xl mx-auto mb-8 leading-relaxed">
          Unified access to molecular interactions, pathways, and annotations from 100+ scientific databases.
        </p>
        
        <div className="flex gap-3 justify-center">
          <Button
            size="lg"
            variant="default"
            className="text-white border-0 hover:opacity-90"
            style={{ backgroundImage: "linear-gradient(135deg, #007B7F 0%, #4A7C2C 50%, #FCCC06 100%)" }}
            asChild
          >
            <Link href="/search">Get started</Link>
          </Button>
        </div>
      </div>
    </div>
  )
}
