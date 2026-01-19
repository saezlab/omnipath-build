import { Suspense } from "react";
import AnnotationsPage from "@/features/explore/annotations-page";

function AnnotationsPageFallback() {
    return (
        <div className="flex-1 flex items-center justify-center">
            <div className="animate-pulse text-muted-foreground">Loading...</div>
        </div>
    );
}

export default function Page() {
    return (
        <Suspense fallback={<AnnotationsPageFallback />}>
            <AnnotationsPage />
        </Suspense>
    );
}
