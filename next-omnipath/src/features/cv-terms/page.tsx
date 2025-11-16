import { SiteLayout } from "@/components/layout/main-layout";
import { ResultCard } from "@/features/search/components/result-card";
import { CvTermAssociatedEntities } from "./components/cv-term-associated-entities";
import { fetchCvTerm } from "./api/queries";
import type { Metadata } from "next";
import { notFound } from "next/navigation";

export const metadata: Metadata = {
  title: "Ontology Term Details | OmniPath Explorer",
  description: "View detailed information about an ontology term",
};

export default async function CvTermDetailsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  
  // Fetch the CV term data
  const cvTerm = await fetchCvTerm(id);
  
  if (!cvTerm) {
    notFound();
  }

  // Get associated entity IDs from the CV term
  const associatedEntityIds = cvTerm.associated_entity_ids || [];

  return (
    <SiteLayout>
      <div className="container py-8 mx-auto">
        {/* Use ResultCard for main ontology term information at the top */}
        <ResultCard result={cvTerm} />
        <h2 className="text-xl font-bold tracking-tight py-8">
          Associated Entities {associatedEntityIds.length > 0 && `(${associatedEntityIds.length})`}
        </h2>
        {/* Use client component with infinite scroll for associated entities */}
        {associatedEntityIds.length > 0 ? (
          <CvTermAssociatedEntities entityIds={associatedEntityIds} />
        ) : (
          <p className="text-muted-foreground">No associated entities found.</p>
        )}
      </div>
    </SiteLayout>
  );
}