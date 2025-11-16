import { SiteLayout } from "@/components/layout/main-layout"
import { GoldTablesSection } from "@/features/home/components/gold-tables-section"

export default function GoldPage() {
  return (
    <SiteLayout showFooter={true}>
      <div className="container py-8 mx-auto">
        <div className="mb-8 text-center">
          <h1 className="text-3xl font-bold tracking-tight mb-2">Gold Layer Tables</h1>
          <p className="text-muted-foreground">
            Application-ready deduplicated data mapped to application models
          </p>
        </div>
        <GoldTablesSection />
      </div>
    </SiteLayout>
  )
}