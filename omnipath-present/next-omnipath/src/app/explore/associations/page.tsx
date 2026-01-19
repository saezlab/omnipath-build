import { Suspense } from "react";
import AssociationsPage from "@/features/explore/associations-page";

function AssociationsPageFallback() {
    return (
        <div className="flex-1 flex items-center justify-center">
            <div className="animate-pulse text-muted-foreground">Loading...</div>
        </div>
    );
}

export default function Page() {
    return (
        <Suspense fallback={<AssociationsPageFallback />}>
            <AssociationsPage />
        </Suspense>
    );
}
