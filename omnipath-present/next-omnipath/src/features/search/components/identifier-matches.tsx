"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ResultCard, type SearchResult } from "./result-card";
import { cn } from "@/lib/utils";

export interface IdentifierMatch {
  identifier: string;
  entityIds: number[];
}

interface IdentifierMatchesProps {
  matches: IdentifierMatch[];
  entities: SearchResult[];
  loading?: boolean;
  error?: string | null;
}

export function IdentifierMatches({ matches, entities, loading = false, error }: IdentifierMatchesProps) {
  const entityMap = new Map<string, SearchResult>();
  for (const entity of entities) {
    const key = (entity.entity_id ?? entity.id)?.toString();
    if (key) {
      entityMap.set(key, entity);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-8 text-muted-foreground">
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
        <span>Looking up identifiers…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
        {error}
      </div>
    );
  }

  if (!matches.length) {
    return (
      <div className="text-muted-foreground py-8 text-sm">Enter identifiers to see matches.</div>
    );
  }

  return (
    <div className="space-y-4">
      {matches.map((match) => {
        const candidates = match.entityIds
          .map((id) => entityMap.get(id.toString()))
          .filter((result): result is SearchResult => Boolean(result));

        const status = (() => {
          if (match.entityIds.length === 0) return "unmapped" as const;
          if (candidates.length === 0) return "missing" as const;
          if (candidates.length === 1) return "resolved" as const;
          return "ambiguous" as const;
        })();

        return (
          <Card key={match.identifier} className="border border-muted">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
              <div className="flex items-center gap-2">
                <CardTitle className="text-base">{match.identifier}</CardTitle>
                <Badge
                  variant={status === "resolved" ? "secondary" : status === "ambiguous" ? "outline" : "destructive"}
                  className={cn(
                    "text-xs",
                    status === "unmapped" && "opacity-80",
                    status === "ambiguous" && "border-dashed"
                  )}
                >
                  {status === "resolved" && "resolved"}
                  {status === "ambiguous" && "multiple matches"}
                  {status === "unmapped" && "no match"}
                  {status === "missing" && "details missing"}
                </Badge>
              </div>
              {match.entityIds.length > 1 && (
                <div className="text-xs text-muted-foreground">{match.entityIds.length} candidates</div>
              )}
            </CardHeader>
            <CardContent className="pt-0">
              {candidates.length > 0 ? (
                <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }}>
                  {candidates.map((candidate) => {
                    const key = (candidate.entity_id ?? candidate.id)?.toString();
                    return key ? <ResultCard key={key} result={candidate} /> : null;
                  })}
                </div>
              ) : (
                <div className="text-sm text-muted-foreground py-3">No candidate details available.</div>
              )}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
