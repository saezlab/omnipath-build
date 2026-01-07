import { Suspense } from "react";
import ExplorePage from "@/features/explore/page";

function ExplorePageFallback() {
  return (
    <div className="flex-1 flex items-center justify-center">
      <div className="animate-pulse text-muted-foreground">Loading...</div>
    </div>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<ExplorePageFallback />}>
      <ExplorePage />
    </Suspense>
  );
}
