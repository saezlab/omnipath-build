"use client"
import React, { useRef, useState } from 'react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { Skeleton } from "@/components/ui/skeleton";
import { useEntity } from "@/hooks/use-entity";
import { ResultCard } from "@/features/search/components/result-card";

interface EntityBadgeProps {
  displayName: string;
  canonicalIdentifier: string;
  geneSymbol?: string;  // Keep for backward compatibility
  uniprotId?: string;   // Keep for backward compatibility
  isFormatted?: boolean; // Whether the text contains <em> tags for highlighting
  showHover?: boolean; // Whether to show hover card with entity details
}

export const EntityBadge: React.FC<EntityBadgeProps> = ({ 
  displayName, 
  canonicalIdentifier, 
  geneSymbol, 
  uniprotId,
  isFormatted = false,
  showHover = true
}) => {
  // Use new props if provided, fallback to old props for backward compatibility
  const name = displayName || geneSymbol || '';
  const identifier = canonicalIdentifier || uniprotId || '';
  
  // Helper function to convert <em> tags to highlighted spans
  const convertEmToHighlight = (text: string) => {
    return text.replace(/<em>/g, '<span class="bg-yellow-200 dark:bg-blue-500 px-1 rounded">').replace(/<\/em>/g, '</span>');
  };
  
  const nameRef = useRef<HTMLSpanElement>(null);
  const identifierRef = useRef<HTMLSpanElement>(null);
  const [isNameTruncated, setIsNameTruncated] = useState(false);
  const [isIdentifierTruncated, setIsIdentifierTruncated] = useState(false);
  const [isHoverOpen, setIsHoverOpen] = useState(false);
  const { data: entity, loading, error } = useEntity(
    isHoverOpen && showHover ? identifier : undefined
  );

  React.useEffect(() => {
    const checkTruncation = () => {
      if (nameRef.current) {
        setIsNameTruncated(nameRef.current.scrollWidth > nameRef.current.clientWidth);
      }
      if (identifierRef.current) {
        setIsIdentifierTruncated(identifierRef.current.scrollWidth > identifierRef.current.clientWidth);
      }
    };

    checkTruncation();
    window.addEventListener('resize', checkTruncation);
    return () => window.removeEventListener('resize', checkTruncation);
  }, [name, identifier]);

  const content = (
    <div className="relative">
      {/* Modern glass-morphism card */}
      <div className="relative bg-gradient-to-br from-slate-50/80 to-slate-100/80 dark:from-slate-800/80 dark:to-slate-900/80 backdrop-blur-sm border border-slate-200/60 dark:border-slate-700/60 rounded-md px-2 py-1 shadow-sm min-w-[80px] w-full">
        
        
        {/* Content */}
        <div className="flex flex-col items-center justify-center min-h-[32px]">
          {/* Primary display - gene symbol or canonical identifier */}
          <div className="h-[14px] flex items-center w-full">
            {isFormatted ? (
              <span 
                ref={nameRef}
                className="text-xs font-medium text-slate-900 dark:text-slate-100 truncate w-full text-center leading-tight"
                dangerouslySetInnerHTML={{ __html: convertEmToHighlight(name || identifier) }}
              />
            ) : (
              <span 
                ref={nameRef}
                className="text-xs font-medium text-slate-900 dark:text-slate-100 truncate w-full text-center leading-tight"
              >
                {name || identifier}
              </span>
            )}
          </div>
          
          {/* Secondary line - canonical identifier only if we have a gene symbol */}
          <div className="h-[14px] flex items-center w-full">
            {name && (
              <>
                {isFormatted ? (
                  <span 
                    ref={identifierRef}
                    className="text-[10px] font-mono text-slate-500 dark:text-slate-400 truncate w-full text-center leading-none"
                    dangerouslySetInnerHTML={{ __html: convertEmToHighlight(identifier) }}
                  />
                ) : (
                  <span 
                    ref={identifierRef}
                    className="text-[10px] font-mono text-slate-500 dark:text-slate-400 truncate w-full text-center leading-none"
                  >
                    {identifier}
                  </span>
                )}
              </>
            )}
          </div>
        </div>

      </div>
    </div>
  );

  // Wrap with hover card if enabled
  if (showHover) {
    const wrappedContent = (
      <HoverCard open={isHoverOpen} onOpenChange={setIsHoverOpen}>
        <HoverCardTrigger asChild>
          {content}
        </HoverCardTrigger>
        <HoverCardContent className="w-[450px] p-0 border-0" align="start">
          {loading ? (
            <div className="p-6 space-y-3">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-full" />
            </div>
          ) : error ? (
            <div className="p-6 text-sm text-muted-foreground">
              Failed to load entity details
            </div>
          ) : entity ? (
            <ResultCard result={{ ...entity, type: 'entity' }} />
          ) : (
            <div className="p-6 text-sm text-muted-foreground">
              No details available
            </div>
          )}
        </HoverCardContent>
      </HoverCard>
    );

    // If also truncated, add tooltip
    if (isNameTruncated || isIdentifierTruncated) {
      return (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              {wrappedContent}
            </TooltipTrigger>
            <TooltipContent className="bg-slate-900 dark:bg-slate-100 text-slate-100 dark:text-slate-900 border-slate-700 dark:border-slate-300">
              <p className="text-sm font-medium">{name}</p>
              <p className="text-xs font-mono text-slate-400 dark:text-slate-600">{identifier}</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      );
    }

    return wrappedContent;
  }

  // No hover card, just tooltip if truncated
  if (isNameTruncated || isIdentifierTruncated) {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            {content}
          </TooltipTrigger>
          <TooltipContent className="bg-slate-900 dark:bg-slate-100 text-slate-100 dark:text-slate-900 border-slate-700 dark:border-slate-300">
            <p className="text-sm font-medium">{name}</p>
            <p className="text-xs font-mono text-slate-400 dark:text-slate-600">{identifier}</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  return content;
}; 