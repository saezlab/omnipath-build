import { fetchDataSource } from "./api/queries";
import { DataSourceDetails } from "./components/data-source-details";
import { notFound } from "next/navigation";
import type { Metadata } from "next";

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
  const source = await fetchDataSource(id);

  if (!source) {
    notFound();
  }

  return <DataSourceDetails source={source} sourceId={id} />;
}