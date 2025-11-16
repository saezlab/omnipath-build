import { getAllDatasources } from "@/features/datasource-explorer/api/datasource-queries"
import { DatasourceExplorer } from "@/features/datasource-explorer/components/datasource-explorer"
import { SiteLayout } from "@/components/layout/main-layout"
import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Datasource Explorer | OmniPath",
  description: "Browse and explore all datasources integrated into OmniPath",
}

export default async function DatasourcesPage() {
  const datasources = await getAllDatasources()

  return (
    <SiteLayout>
      <DatasourceExplorer datasources={datasources} />
    </SiteLayout>
  )
}