import { SiteLayout } from "@/components/layout/main-layout";
import { OntologyExplorer } from "@/features/entity-details/components/ontology-explorer";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EntityInteractionsSearch } from "@/features/interactions-search/components/entity-interactions-search";
import { ResultCard } from "@/features/search/components/result-card";
import { SearchResults } from "@/features/search/components/search-results";
import { fetchEntity, fetchAssociatedEntities } from "./api/queries";
import type { Metadata } from "next";
import { notFound } from "next/navigation";

export const metadata: Metadata = {
  title: "Entity Details | OmniPath Explorer",
  description: "View detailed information about a biological entity",
};

export default async function EntityDetailsPage({ 
  params 
}: { 
  params: Promise<{ entity_type: string; id: string }> 
}) {
  const { entity_type, id } = await params;
  const entity = await fetchEntity(id);

  if (!entity) {
    notFound();
  }
  
  // Check if the URL entity type matches the actual entity type
  const entityTypeLower = entity.entity_type_name?.toLowerCase() || '';
  const expectedUrlSegment = entityTypeLower.replace(/\s+/g, '-'); // Convert "protein family" to "protein-family"
  
  // Allow both direct entity type match and the generic "entities" route
  if (entity_type !== 'entities' && entity_type !== expectedUrlSegment) {
    notFound();
  }
  
  // Determine if this entity type has members (complexes and protein families)
  const hasMembers = entityTypeLower === 'complex' || entityTypeLower === 'molecule set';
  
  // Fetch associated entities if this entity type has members
  const associatedEntities = hasMembers ? await fetchAssociatedEntities(id) : [];

  return (
    <SiteLayout>
      <div className="container py-8 mx-auto">
        <ResultCard result={entity} />

        <div className="grid gap-4 mt-6">
            <Tabs defaultValue="interactions">
              <TabsList className="grid w-full grid-cols-2">
                <TabsTrigger value="interactions">Interactions</TabsTrigger>
                {hasMembers ? (
                  <TabsTrigger value="members">
                    {entityTypeLower === 'complex' ? 'Complex Members' : 'Family Members'}
                  </TabsTrigger>
                ) : (
                  <TabsTrigger value="ontology">Ontology Terms</TabsTrigger>
                )}
              </TabsList>
              
              {hasMembers ? (
                <TabsContent value="members" className="mt-4">
                  <h2 className="text-xl font-bold tracking-tight mb-4">
                    {entityTypeLower === 'complex' ? 'Complex Members' : 'Family Members'}
                  </h2>
                  {associatedEntities.length > 0 ? (
                    <SearchResults results={associatedEntities} />
                  ) : (
                    <div className="text-center py-8">
                      <p className="text-slate-500">
                        No members found for this {entityTypeLower}.
                      </p>
                    </div>
                  )}
                </TabsContent>
              ) : (
                <TabsContent value="ontology" className="mt-4">
                  <OntologyExplorer
                    cvTermIds={entity.cv_term_ids || entity.cvTermIds || []}
                    entityId={entity.id}
                  />
                </TabsContent>
              )}
              
              <TabsContent value="interactions" className="mt-4">
                <EntityInteractionsSearch 
                  entityId={id} 
                  entityName={entity.display_name || entity.canonical_identifier}
                />
              </TabsContent>
            </Tabs>
        </div>
      </div>
    </SiteLayout>
  );
}