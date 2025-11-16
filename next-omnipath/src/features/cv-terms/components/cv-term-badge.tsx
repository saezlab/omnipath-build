"use client";

import * as React from "react";
import { Badge, badgeVariants } from "@/components/ui/badge";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { Skeleton } from "@/components/ui/skeleton";
import { useCvTerm } from "@/hooks/use-cv-term";
import { cn } from "@/lib/utils";
import type { VariantProps } from "class-variance-authority";

interface CvTermBadgeProps extends React.ComponentProps<"span">, VariantProps<typeof badgeVariants> {
  cvTermId?: string | number;
  cvTermName: string;
  showHover?: boolean;
  className?: string;
}

export function CvTermBadge({ 
  cvTermId, 
  cvTermName, 
  showHover = true,
  variant = "outline",
  className,
  ...props 
}: CvTermBadgeProps) {
  const [isOpen, setIsOpen] = React.useState(false);
  const { data: cvTerm, loading, error } = useCvTerm(
    isOpen && cvTermId ? String(cvTermId) : undefined
  );

  // If no ID provided or hover disabled, just render a regular badge
  if (!cvTermId || !showHover) {
    return (
      <Badge variant={variant} className={cn("block overflow-hidden", className)} {...props}>
        <span className="truncate">{cvTermName}</span>
      </Badge>
    );
  }

  return (
    <HoverCard open={isOpen} onOpenChange={setIsOpen}>
      <HoverCardTrigger asChild>
        <Badge 
          variant={variant} 
          className={cn("inline-flex items-center min-w-0 max-w-full", className)} 
          {...props}
        >
          <span className="truncate min-w-0 mr-3">{cvTermName}</span>
        </Badge>
      </HoverCardTrigger>
      <HoverCardContent className="w-80" align="start">
        {loading ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-full" />
          </div>
        ) : error ? (
          <div className="text-sm text-muted-foreground">
            Failed to load term details
          </div>
        ) : cvTerm ? (
          <div className="space-y-3">
            <div>
              <h4 className="font-semibold text-sm">{cvTerm.name}</h4>
              {cvTerm.namespace && (
                <p className="text-xs text-muted-foreground">{cvTerm.namespace}</p>
              )}
            </div>

            {cvTerm.definition && (
              <div>
                <p className="text-xs font-medium mb-1">Definition</p>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  {cvTerm.definition}
                </p>
              </div>
            )}

            {cvTerm.synonyms && cvTerm.synonyms.length > 0 && (
              <div>
                <p className="text-xs font-medium mb-1">Also known as</p>
                <div className="flex flex-wrap gap-1">
                  {cvTerm.synonyms.slice(0, 5).map((synonym, idx) => (
                    <Badge key={idx} variant="secondary" className="text-xs px-1.5 py-0">
                      {synonym}
                    </Badge>
                  ))}
                  {cvTerm.synonyms.length > 5 && (
                    <span className="text-xs text-muted-foreground">
                      +{cvTerm.synonyms.length - 5} more
                    </span>
                  )}
                </div>
              </div>
            )}

            {cvTerm.associated_entity_ids && cvTerm.associated_entity_ids.length > 0 && (
              <div className="pt-2 border-t">
                <p className="text-xs text-muted-foreground">
                  Associated with {cvTerm.associated_entity_ids.length} entities
                </p>
              </div>
            )}
          </div>
        ) : (
          <div className="text-sm text-muted-foreground">
            No details available
          </div>
        )}
      </HoverCardContent>
    </HoverCard>
  );
}