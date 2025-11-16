"use client"

import { useState } from "react"
import { 
  BookOpen, 
  Table2, 
  Layers, 
  Users, 
  FileText, 
  Link2, 
  Activity, 
  Network, 
  Shield, 
  BarChart3, 
  TrendingUp, 
  CheckCircle 
} from "lucide-react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { GoldTablesGrid } from "@/features/data-transparency/components/gold-tables-grid"

const goldTables = [
  { value: "cv_namespace", label: "CV Namespace", icon: BookOpen },
  { value: "cv_term", label: "CV Term", icon: Table2 },
  { value: "cv_term_hierarchy", label: "CV Hierarchy", icon: Layers },
  { value: "entity", label: "Entity", icon: Users },
  { value: "entity_identifier", label: "Entity Identifier", icon: FileText },
  { value: "entity_membership", label: "Entity Membership", icon: Link2 },
  { value: "protein_details", label: "Protein Details", icon: Activity },
  { value: "reference", label: "Reference", icon: FileText },
  { value: "interaction_canonical", label: "Interaction Canonical", icon: Network },
  { value: "interaction_evidence", label: "Interaction Evidence", icon: Shield },
  { value: "entity_interaction_stats", label: "Entity Stats", icon: BarChart3 },
  { value: "network_metrics", label: "Network Metrics", icon: TrendingUp },
  { value: "data_quality_metrics", label: "Data Quality", icon: CheckCircle },
  
]

export function GoldTablesSection() {
  const [activeTab, setActiveTab] = useState("cv_namespace")
  const [loadedTabs, setLoadedTabs] = useState<Set<string>>(new Set(["cv_namespace"]))

  const handleTabChange = (value: string) => {
    setActiveTab(value)
    setLoadedTabs(prev => new Set([...prev, value]))
  }

  return (
    <Tabs value={activeTab} onValueChange={handleTabChange} className="w-full">
      <div className="flex flex-col items-center gap-6">
        <h2 className="text-2xl font-bold tracking-tight">Gold</h2>
        <div className="w-full overflow-x-auto">
          <TabsList className="inline-flex h-10 items-center justify-start rounded-md bg-muted p-1 text-muted-foreground min-w-max">
            {goldTables.map(({ value, label, icon: Icon }) => (
              <TabsTrigger key={value} value={value} className="flex items-center gap-2 whitespace-nowrap">
                <Icon className="h-4 w-4" />
                {label}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
      </div>
      <div className="mt-6">
        {goldTables.map(({ value }) => (
          <TabsContent key={value} value={value} className="mt-0">
            {loadedTabs.has(value) ? (
              <GoldTablesGrid tableName={value} />
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