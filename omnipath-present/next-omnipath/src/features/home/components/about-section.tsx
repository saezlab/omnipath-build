import { Network, Layers, Database, Tag, Users } from "lucide-react"
import { Button } from "@/components/ui/button"

export function AboutSection() {
  return (
    <section className="container px-4 py-16 mt-20">
      <div className="grid md:grid-cols-2 gap-12 items-start">
        <div>
          <h2 className="text-2xl font-medium tracking-tight mb-4">
            Data Integration
          </h2>
          <p className="text-muted-foreground mb-8 leading-relaxed">
            OmniPath consolidates molecular biology data from 100+ scientific data repositories into one unified resource.
          </p>
          <div className="space-y-3">
            <div className="flex items-center gap-3 text-sm">
              <Network className="h-4 w-4 text-muted-foreground" />
              <span>Signaling networks and molecular interactions</span>
            </div>
            <div className="flex items-center gap-3 text-sm">
              <Layers className="h-4 w-4 text-muted-foreground" />
              <span>Post-translational modification networks</span>
            </div>
            <div className="flex items-center gap-3 text-sm">
              <Database className="h-4 w-4 text-muted-foreground" />
              <span>Protein complexes and assemblies</span>
            </div>
            <div className="flex items-center gap-3 text-sm">
              <Tag className="h-4 w-4 text-muted-foreground" />
              <span>Functional annotations and localizations</span>
            </div>
            <div className="flex items-center gap-3 text-sm">
              <Users className="h-4 w-4 text-muted-foreground" />
              <span>Intercellular communication roles</span>
            </div>
          </div>
          <Button variant="outline" className="mt-8">
            Read publication
          </Button>
        </div>
        <div className="bg-muted/30 rounded-lg p-6 border border-border/40">
          <h3 className="font-medium text-sm uppercase tracking-wide text-muted-foreground mb-6">Platform Statistics</h3>
          <div className="grid grid-cols-2 gap-6">
            <div className="text-center">
              <div className="text-2xl font-medium mb-1">100+</div>
              <div className="text-xs text-muted-foreground">Data sources</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-medium mb-1">1M+</div>
              <div className="text-xs text-muted-foreground">Interactions</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-medium mb-1">60k+</div>
              <div className="text-xs text-muted-foreground">References</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-medium mb-1">1</div>
              <div className="text-xs text-muted-foreground">Database</div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

