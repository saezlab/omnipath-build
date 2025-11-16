"use client"

import { useState } from "react"
import { Network, Users, Table2 } from "lucide-react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { SilverTablesGrid } from "@/features/data-transparency/components/silver-tables-grid"

const silverTables = [
  { value: "interactions", label: "Interactions", icon: Network },
  { value: "entities", label: "Entities", icon: Users },
  { value: "cv_term", label: "CV Term", icon: Table2 },
]

export function SilverTablesSection() {
  const [activeTab, setActiveTab] = useState("interactions")
  const [loadedTabs, setLoadedTabs] = useState<Set<string>>(new Set(["interactions"]))

  const handleTabChange = (value: string) => {
    setActiveTab(value)
    setLoadedTabs(prev => new Set([...prev, value]))
  }

  return (
    <Tabs value={activeTab} onValueChange={handleTabChange} className="w-full">
      <div className="flex flex-col items-center gap-6">
        <h2 className="text-2xl font-bold tracking-tight">Silver</h2>
        <TabsList className="grid w-full max-w-2xl grid-cols-3">
          {silverTables.map(({ value, label, icon: Icon }) => (
            <TabsTrigger key={value} value={value} className="flex items-center gap-2">
              <Icon className="h-4 w-4" />
              {label}
            </TabsTrigger>
          ))}
        </TabsList>
      </div>
      <div className="mt-6">
        {silverTables.map(({ value }) => (
          <TabsContent key={value} value={value} className="mt-0">
            {loadedTabs.has(value) ? (
              <SilverTablesGrid tableName={value} />
            ) : (
              <div className="flex items-center justify-center py-10">
                <p className="text-muted-foreground">Loading...</p>
              </div>
            )}
          </TabsContent>
        ))}
      </div>
    </Tabs>
  )
}