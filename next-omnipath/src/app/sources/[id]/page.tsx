import { getDatasourceById, getBronzeTableSamples } from "@/features/datasource-explorer/api/datasource-queries";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { SiteLayout } from "@/components/layout/main-layout";
import { DataSourceDetail } from "@/features/datasource-explorer/components/datasource-detail";

export const metadata: Metadata = {
  title: "Data Source Details | OmniPath",
  description: "View detailed information about data sources",
};

export default async function DataSourcePage({ 
  params 
}: { 
  params: Promise<{ id: string }> 
}) {
  const { id } = await params;
  const [datasource, bronzeSamples] = await Promise.all([
    getDatasourceById(id),
    getBronzeTableSamples(id)
  ]);

  if (!datasource) {
    notFound();
  }

  return (
    <SiteLayout>
      <DataSourceDetail 
        datasource={datasource} 
        bronzeSamples={bronzeSamples}
      />
    </SiteLayout>
  );
}
