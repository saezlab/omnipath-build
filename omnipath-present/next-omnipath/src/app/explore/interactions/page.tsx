import { Suspense } from "react";
import InteractionsPage from "@/features/explore/interactions-page";

function InteractionsPageFallback() {
    return (
        <div className="flex-1 flex items-center justify-center">
            <div className="animate-pulse text-muted-foreground">Loading...</div>
        </div>
    );
}

export default function Page() {
    return (
        <Suspense fallback={<InteractionsPageFallback />}>
            <InteractionsPage useEntityFilters={false} />
        </Suspense>
    );
}
