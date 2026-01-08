"use client"

import { SiteLayout } from "@/components/layout/main-layout"
import { HeroSection } from "./hero-section"
  
export function HomePage() {
  return (
    <SiteLayout showFooter={true}>
      <HeroSection />
    </SiteLayout>
  )
}

