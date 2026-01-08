"use client"
import React, { useRef, useState } from 'react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { useEntity } from "@/hooks/use-entity";
import { ResultCard } from "@/features/search/components/result-card";
import { FlaskConical, Dna, CircleDot, Waypoints, Shapes, HelpCircle } from "lucide-react";

// Map entity types to icons and colors
const entityTypeConfig: Record<string, { icon: React.ElementType; color: string; bgColor: string; label: string }> = {
  'protein': { icon: CircleDot, color: 'text-blue-500', bgColor: 'from-blue-50/80 to-blue-100/80 dark:from-blue-900/30 dark:to-blue-800/30', label: 'Protein' },
  'smallmolecule': { icon: FlaskConical, color: 'text-green-500', bgColor: 'from-green-50/80 to-green-100/80 dark:from-green-900/30 dark:to-green-800/30', label: 'Small Molecule' },
  'small_molecule': { icon: FlaskConical, color: 'text-green-500', bgColor: 'from-green-50/80 to-green-100/80 dark:from-green-900/30 dark:to-green-800/30', label: 'Small Molecule' },
  'compound': { icon: FlaskConical, color: 'text-green-500', bgColor: 'from-green-50/80 to-green-100/80 dark:from-green-900/30 dark:to-green-800/30', label: 'Compound' },
  'metabolite': { icon: FlaskConical, color: 'text-green-500', bgColor: 'from-green-50/80 to-green-100/80 dark:from-green-900/30 dark:to-green-800/30', label: 'Metabolite' },
  'drug': { icon: FlaskConical, color: 'text-purple-500', bgColor: 'from-purple-50/80 to-purple-100/80 dark:from-purple-900/30 dark:to-purple-800/30', label: 'Drug' },
  'lipid': { icon: FlaskConical, color: 'text-yellow-600', bgColor: 'from-yellow-50/80 to-yellow-100/80 dark:from-yellow-900/30 dark:to-yellow-800/30', label: 'Lipid' },
  'gene': { icon: Dna, color: 'text-orange-500', bgColor: 'from-orange-50/80 to-orange-100/80 dark:from-orange-900/30 dark:to-orange-800/30', label: 'Gene' },
  'complex': { icon: Shapes, color: 'text-indigo-500', bgColor: 'from-indigo-50/80 to-indigo-100/80 dark:from-indigo-900/30 dark:to-indigo-800/30', label: 'Complex' },
  'pathway': { icon: Waypoints, color: 'text-cyan-500', bgColor: 'from-cyan-50/80 to-cyan-100/80 dark:from-cyan-900/30 dark:to-cyan-800/30', label: 'Pathway' },
  'reaction': { icon: Waypoints, color: 'text-pink-500', bgColor: 'from-pink-50/80 to-pink-100/80 dark:from-pink-900/30 dark:to-pink-800/30', label: 'Reaction' },
};

const defaultConfig = { icon: HelpCircle, color: 'text-slate-500', bgColor: 'from-slate-50/80 to-slate-100/80 dark:from-slate-800/80 dark:to-slate-900/80', label: 'Entity' };

function getEntityTypeConfig(entityType: string | undefined) {
  if (!entityType) return defaultConfig;
  const key = entityType.toLowerCase();
  return entityTypeConfig[key] || defaultConfig;
}

interface EntityBadgeProps {
  displayName: string;
  canonicalIdentifier: string;
  entityType?: string; // e.g., "Protein", "SmallMolecule"
  geneSymbol?: string;  // Keep for backward compatibility
  uniprotId?: string;   // Keep for backward compatibility
  isFormatted?: boolean; // Whether the text contains <em> tags for highlighting
  showHover?: boolean; // Whether to show hover card with entity details
}

export const EntityBadge: React.FC<EntityBadgeProps> = ({
  displayName,
  canonicalIdentifier,
  entityType,
  geneSymbol,
  uniprotId,
  isFormatted = false,
  showHover = true
}) => {
  // Use new props if provided, fallback to old props for backward compatibility
  const name = displayName || geneSymbol || '';
  const identifier = canonicalIdentifier || uniprotId || '';

  // Check if name and identifier are the same (or identifier is just a number)
  const isDuplicate = name === identifier || /^\d+$/.test(identifier);

  // Get type config for icon and colors
  const typeConfig = getEntityTypeConfig(entityType);
  const TypeIcon = typeConfig.icon;

  // Helper function to convert <em> tags to highlighted spans
  const convertEmToHighlight = (text: string) => {
    return text.replace(/<em>/g, '<span class="bg-yellow-200 dark:bg-blue-500 px-1 rounded">').replace(/<\/em>/g, '</span>');
  };

  const nameRef = useRef<HTMLSpanElement>(null);
  const identifierRef = useRef<HTMLSpanElement>(null);
  const [isNameTruncated, setIsNameTruncated] = useState(false);
  const [isIdentifierTruncated, setIsIdentifierTruncated] = useState(false);
  const [isHoverOpen, setIsHoverOpen] = useState(false);
  const { data: entity } = useEntity(
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
      {/* Modern glass-morphism card with type-based coloring */}
      <div className={`relative bg-gradient-to-br ${typeConfig.bgColor} backdrop-blur-sm border border-slate-200/60 dark:border-slate-700/60 rounded-md px-2 py-1 shadow-sm min-w-[80px] w-full`}>

        {/* Content with type icon */}
        <div className="flex items-center gap-1.5 min-h-[32px]">
          {/* Type icon with tooltip */}
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <TypeIcon className={`h-4 w-4 ${typeConfig.color} shrink-0`} />
              </TooltipTrigger>
              <TooltipContent side="top" className="text-xs">
                {typeConfig.label}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>

          {/* Name content */}
          <div className="flex flex-col justify-center flex-1 min-w-0">
            {/* Primary display - gene symbol or canonical identifier */}
            <div className="flex items-center">
              {isFormatted ? (
                <span
                  ref={nameRef}
                  className="text-xs font-medium text-slate-900 dark:text-slate-100 truncate leading-tight"
                  dangerouslySetInnerHTML={{ __html: convertEmToHighlight(name || identifier) }}
                />
              ) : (
                <span
                  ref={nameRef}
                  className="text-xs font-medium text-slate-900 dark:text-slate-100 truncate leading-tight"
                >
                  {name || identifier}
                </span>
              )}
            </div>

            {/* Secondary line - canonical identifier only if different from name */}
            {name && !isDuplicate && (
              <div className="flex items-center">
                {isFormatted ? (
                  <span
                    ref={identifierRef}
                    className="text-[10px] font-mono text-slate-500 dark:text-slate-400 truncate leading-none"
                    dangerouslySetInnerHTML={{ __html: convertEmToHighlight(identifier) }}
                  />
                ) : (
                  <span
                    ref={identifierRef}
                    className="text-[10px] font-mono text-slate-500 dark:text-slate-400 truncate leading-none"
                  >
                    {identifier}
                  </span>
                )}
              </div>
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
          {entity ? (
            <ResultCard result={{ ...entity, type: 'entity' }} />
          ) : null}
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
              {!isDuplicate && <p className="text-xs font-mono text-slate-400 dark:text-slate-600">{identifier}</p>}
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
            {!isDuplicate && <p className="text-xs font-mono text-slate-400 dark:text-slate-600">{identifier}</p>}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  return content;
}; 