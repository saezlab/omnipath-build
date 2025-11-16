import { Skeleton } from "@/components/ui/skeleton";
import { TableSkeleton } from "@/components/table-skeleton";

export function InteractionsSearchSkeleton() {
  return (
    <div className="space-y-6">
      {/* Search and filter controls skeleton */}
      <div className="flex gap-4">
        <Skeleton className="h-10 flex-1" />
        <Skeleton className="h-10 w-32" />
      </div>
      
      {/* Results table skeleton */}
      <TableSkeleton />
    </div>
  );
}