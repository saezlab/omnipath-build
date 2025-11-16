import { Metadata } from "next";
import { Suspense } from "react";
import { InteractionsSearch } from "./components/interactions-search";
import { SiteLayout } from "@/components/layout/main-layout";
import { InteractionsSearchSkeleton } from "./skeleton";

export const metadata: Metadata = {
  title: "Search Interactions | OmniPath",
  description: "Search and filter biological interactions using OmniPath's Meilisearch index",
};

export default function InteractionsSearchPage() {
  return (
    <SiteLayout>
      <div className="container mx-auto py-6">
        <Suspense fallback={<InteractionsSearchSkeleton />}>
          <InteractionsSearch />
        </Suspense>
      </div>
    </SiteLayout>
  );
}