"use client"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { ArrowRight, Database, FileText, Filter } from "lucide-react"
import Link from "next/link"
import React from "react"

interface LayerCardProps {
  title: string
  description: string
  icon: React.ReactNode
  bgClass: string
  buttonText: string
  href: string
}

function LayerCard({ title, description, icon, bgClass, buttonText, href }: LayerCardProps) {
  return (
    <Card className={`${bgClass} border-2 flex-1 min-w-[280px]`}>
      <CardHeader className="pb-3">
        <div className="flex items-center gap-3">
          {icon}
          <CardTitle className="text-lg">{title}</CardTitle>
        </div>
        <CardDescription className="text-sm">
          {description}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Button asChild className="w-full">
          <Link href={href}>{buttonText}</Link>
        </Button>
      </CardContent>
    </Card>
  )
}

function FlowArrow() {
  return (
    <div className="flex items-center justify-center px-4 self-center">
      <ArrowRight className="h-6 w-6 text-muted-foreground" />
    </div>
  )
}

export function DataJourneyFlow() {
  return (
    <div className="w-full">
      <div className="flex flex-col lg:flex-row items-center gap-4 lg:gap-0">
        <LayerCard
          title="Bronze Layer"
          description="Raw source data organized by category"
          icon={<FileText className="h-5 w-5 text-orange-600" />}
          bgClass="bg-gradient-to-br from-orange-50 to-amber-50 dark:from-orange-900/20 dark:to-amber-900/20 border-orange-200"
          buttonText="Browse Datasources"
          href="/sources"
        />
        
        <div className="hidden lg:block">
          <FlowArrow />
        </div>
        <div className="lg:hidden flex justify-center">
          <div className="rotate-90">
            <ArrowRight className="h-6 w-6 text-muted-foreground" />
          </div>
        </div>
        
        <LayerCard
          title="Silver Layer"
          description="Unified schema with standardized identifiers"
          icon={<Filter className="h-5 w-5 text-gray-600" />}
          bgClass="bg-gradient-to-br from-gray-50 to-slate-50 dark:from-gray-800/50 dark:to-slate-800/50 border-gray-300"
          buttonText="View Silver Tables"
          href="/silver"
        />
        
        <div className="hidden lg:block">
          <FlowArrow />
        </div>
        <div className="lg:hidden flex justify-center">
          <div className="rotate-90">
            <ArrowRight className="h-6 w-6 text-muted-foreground" />
          </div>
        </div>
        
        <LayerCard
          title="Gold Layer"
          description="Application-ready deduplicated data"
          icon={<Database className="h-5 w-5 text-yellow-700" />}
          bgClass="bg-gradient-to-br from-yellow-50 to-amber-100 dark:from-yellow-900/20 dark:to-amber-900/30 border-yellow-300"
          buttonText="View Gold Tables"
          href="/gold"
        />
      </div>
      
    </div>
  )
}