"use client"

import { DataJourneyFlow } from "@/features/home/components/data-journey-flow"
import { SiteLayout } from "@/components/layout/main-layout"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Download,
  Eye,
  FileText,
  Filter,
  MessageSquare,
  Network,
  Tag
} from "lucide-react"
import { AboutSection } from "./about-section"
import { AIAssistantCard } from "./ai-assistant-card"
import { FeatureCard } from "./feature-card"
import { HeroSection } from "./hero-section"
  
export function HomePage() {
  return (
    <SiteLayout showFooter={true}>
      <HeroSection />
      <div className="container py-8 mx-auto">
        <section>
          <Card>
            <CardHeader>
              <CardTitle>Data Journey Overview</CardTitle>
              <CardDescription>Visualizing the complete flow of data from source to unified database</CardDescription>
            </CardHeader>
            <CardContent>
              <DataJourneyFlow />
            </CardContent>
          </Card>
        </section>



        <section className="mt-12">
        <div className="grid gap-8 md:grid-cols-2">
          <FeatureCard
            title="Interaction Search"
            description="Search and explore molecular interactions across integrated databases"
            features={[
              {
                icon: <Tag className="h-4 w-4" />,
                title: "Multi-target search",
                description: "Query multiple identifiers",
              },
              {
                icon: <Network className="h-4 w-4" />,
                title: "Network visualization",
                description: "Interactive relationship maps",
              },
              {
                icon: <FileText className="h-4 w-4" />,
                title: "Evidence tracking",
                description: "Source attribution",
              },
              {
                icon: <Download className="h-4 w-4" />,
                title: "Export formats",
                description: "CSV, JSON, GraphML",
              },
            ]}
            href="/interactions/search"
            buttonText="Search interactions"
          />
          
          <FeatureCard
            title="Entity Profiles"
            description="Detailed information on proteins, genes, and molecular complexes"
            features={[
              {
                icon: <Eye className="h-4 w-4" />,
                title: "Comprehensive data",
                description: "Function, structure, localization",
              },
              {
                icon: <Network className="h-4 w-4" />,
                title: "Interaction networks",
                description: "All known relationships",
              },
              {
                icon: <Filter className="h-4 w-4" />,
                title: "Advanced filters",
                description: "Type, source, confidence",
              },
              {
                icon: <MessageSquare className="h-4 w-4" />,
                title: "AI assistance",
                description: "Context-aware queries",
              },
            ]}
            href="/search"
            buttonText="Browse entities"
          />
        </div>

        <div className="mt-12 flex justify-center">
          <AIAssistantCard />
        </div>

        <AboutSection />
        </section>
      </div>
    </SiteLayout>
  )
}

