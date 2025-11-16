import { Badge } from "@/components/ui/badge";
import { 
  Card, 
  CardContent, 
  CardFooter,
  CardHeader, 
  CardTitle 
} from "@/components/ui/card";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { ScrollArea } from "@/components/ui/scroll-area";
import React from "react";
import { Network, Tag } from "lucide-react";

// Helper function to convert <em> tags to highlighted spans
const convertEmToHighlight = (text: string) => {
  return text.replace(/<em>/g, '<span class="bg-yellow-200 dark:bg-blue-500 px-1 rounded">').replace(/<\/em>/g, '</span>');
};

export interface SearchResult {
  id: string;
  type?: string;
  _formatted?: {
    description?: string;
    all_identifiers?: string[];
    ncbi_tax_name?: string;
    synonyms?: string[];
    namespace_name?: string;
    definition?: string;
    gene_symbol?: string;
    canonical_identifier?: string;
    name?: string;
    [key: string]: unknown;
  };
  description?: string;
  all_identifiers?: string[];
  entity_type_name?: string;
  ncbi_tax_name?: string;
  synonyms?: string[];
  namespace_name?: string;
  definition?: string;
  gene_symbol?: string;
  canonical_identifier?: string;
  name?: string;
  is_annotated?: boolean;
  associated_entity_ids?: string[];
  interaction_ids?: string[];
  [key: string]: unknown; // Add index signature for compatibility with DataRow
}

export function ResultCard({ result }: { result: SearchResult }) {
  const type = result.type || "entity";

  // Extract data based on type
  const description = result._formatted?.description || result.description;
  const allIdentifiers = result._formatted?.all_identifiers || result.all_identifiers || [];
  const entityTypeName = result.entity_type_name;
  const ncbiTaxName = result._formatted?.ncbi_tax_name || result.ncbi_tax_name;
  const synonyms = result._formatted?.synonyms || result.synonyms;
  const namespaceName = result._formatted?.namespace_name || result.namespace_name;
  const definition = result._formatted?.definition || result.definition;
  
  // Extract gene symbol or determine title
  let title = "";
  let subtitle = "";
  let primaryIdentifier = "";
  const geneSymbol = result._formatted?.gene_symbol || result.gene_symbol;
  
  if (type === 'entity') {
    // Set primary identifier (canonical)
    primaryIdentifier = result._formatted?.canonical_identifier || result.canonical_identifier || allIdentifiers[0] || "";
    
    // Combine gene symbol or canonical identifier with primary identifier
    const displayName = geneSymbol || result._formatted?.canonical_identifier || result.canonical_identifier || allIdentifiers[0] || `Entity ${result.id}`;
    const formattedDisplayName = result._formatted ? convertEmToHighlight(displayName) : displayName;
    title = primaryIdentifier && primaryIdentifier !== displayName ? `${formattedDisplayName} <span class="text-sm text-muted-foreground">(${primaryIdentifier})</span>` : formattedDisplayName;
    
    // Create subtitle
    subtitle = entityTypeName || "Entity";
    if (ncbiTaxName) {
      subtitle += ` (${ncbiTaxName})`;
    }
  } else if (type === 'cv_term') {
    const displayName = result._formatted?.name || result.name || `Term ${result.id}`;
    title = result._formatted ? convertEmToHighlight(displayName) : displayName;
    subtitle = namespaceName || "Ontology Term";
    primaryIdentifier = result.id;
  }

  // Stats
  const interactionCount = result.interaction_ids?.length || 0;
  const entityCount = result.associated_entity_ids?.length || 0;

  return (
    <Card className={`flex flex-col hover:shadow-md transition-shadow h-full result-card ${type === 'cv_term' ? 'cursor-pointer' : ''}`}>
      <CardHeader className="relative space-y-0 p-2.5 border-b shrink-0">
        <div className="flex flex-col gap-1">
          <div className="flex items-start justify-between">
            <div className="min-w-0 flex-1">
              <CardTitle className="text-lg line-clamp-1">
                <span dangerouslySetInnerHTML={{ __html: title }} />
              </CardTitle>
            </div>
            <Badge
              variant="secondary"
              className="ml-2 flex-shrink-0"
            >
              {subtitle}
            </Badge>
          </div>
        </div>
      </CardHeader>

      {(description || definition) && (
        <div className="flex flex-col min-h-0 flex-grow">
          <CardContent className="px-4 overflow-hidden flex-grow min-h-0">
            {/* Description */}
            <ScrollArea className="h-32 w-full">
              <p className="text-sm text-muted-foreground">
                <span dangerouslySetInnerHTML={{ __html: convertEmToHighlight(definition || description || '') }} />
              </p>
            </ScrollArea>
          </CardContent>
        </div>
      )}

      <CardFooter className={`flex items-center justify-between shrink-0 ${(description || definition) ? 'border-t' : ''}`}>
        {/* Stats section */}
        <div className="flex items-center gap-4 text-sm">
          {type === 'entity' && interactionCount > 0 && (
            <div className="flex items-center gap-1.5 text-muted-foreground">
              <Network className="h-4 w-4" />
              <span>{interactionCount} interaction{interactionCount === 1 ? "" : "s"}</span>
            </div>
          )}
          {type === 'cv_term' && entityCount > 0 && (
            <div className="flex items-center gap-1.5 text-muted-foreground">
              <Tag className="h-4 w-4" />
              <span>{entityCount} entit{entityCount === 1 ? "y" : "ies"}</span>
            </div>
          )}
        </div>

        {/* Show identifiers/synonyms count on hover */}
        {((type === 'entity' && allIdentifiers && allIdentifiers.length > 0) ||
          (type === 'cv_term' && synonyms && synonyms.length > 0)) && (
          <HoverCard>
            <HoverCardTrigger asChild>
              <div className="text-sm text-muted-foreground hover:text-foreground transition-colors">
                {type === 'entity' ? (
                  <span>{allIdentifiers.length} identifier{allIdentifiers.length === 1 ? '' : 's'}</span>
                ) : (
                  <span>{synonyms?.length || 0} synonym{synonyms?.length === 1 ? '' : 's'}</span>
                )}
              </div>
            </HoverCardTrigger>
            <HoverCardContent className="w-80 max-h-96 overflow-y-auto">
              <div className="space-y-3">
                {type === 'entity' && allIdentifiers && (
                  <div>
                    <h4 className="text-sm font-semibold mb-2">All Identifiers ({allIdentifiers.length})</h4>
                    <p className="text-sm text-muted-foreground">
                      <span dangerouslySetInnerHTML={{ __html: convertEmToHighlight(allIdentifiers.join(', ')) }} />
                    </p>
                  </div>
                )}
                {type === 'cv_term' && synonyms && (
                  <div>
                    <h4 className="text-sm font-semibold mb-2">All Synonyms ({synonyms.length})</h4>
                    <p className="text-sm text-muted-foreground">
                      <span dangerouslySetInnerHTML={{ __html: convertEmToHighlight(synonyms.join(', ')) }} />
                    </p>
                  </div>
                )}
              </div>
            </HoverCardContent>
          </HoverCard>
        )}
      </CardFooter>
    </Card>
  );
}