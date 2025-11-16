import { SiteLayout } from "@/components/layout/main-layout"
import { SilverTablesSection } from "@/features/home/components/silver-tables-section"

export default function SilverPage() {
  return (
    <SiteLayout showFooter={true}>
      <div className="container py-8 mx-auto">
        <div className="mb-8 text-center">
          <h1 className="text-3xl font-bold tracking-tight mb-2">Silver Layer Tables</h1>
          <p className="text-muted-foreground">
            Unified data with standardized schema and applied transformations
          </p>
        </div>
        <SilverTablesSection />
      </div>
    </SiteLayout>
  )
}