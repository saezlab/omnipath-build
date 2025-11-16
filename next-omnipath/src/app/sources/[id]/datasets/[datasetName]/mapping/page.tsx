import { getDatasourceById } from '@/features/datasource-explorer/api/datasource-queries';
import { MappingConfiguration } from '@/features/datasource-explorer/dataset-mapping/components/mapping-configuration-simple';
import { notFound } from 'next/navigation';

interface PageProps {
  params: Promise<{
    id: string;
    datasetName: string;
  }>;
}

export default async function DatasetMappingPage({ params }: PageProps) {
  const { id, datasetName } = await params;
  const datasource = await getDatasourceById(id);
  
  if (!datasource) {
    notFound();
  }
  
  const dataset = datasource.datasets.find(d => d.name === datasetName);
  
  if (!dataset) {
    notFound();
  }
  
  return (
    <MappingConfiguration 
      datasource={datasource}
      dataset={dataset}
    />
  );
}