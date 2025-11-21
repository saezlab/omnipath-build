"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { Network, Tag, Shapes, FileText, Database, Plus, Check } from "lucide-react";
import { useEntitySelection } from "@/contexts/entity-selection-context";

// Helper function to convert <em> tags to highlighted spans
const convertEmToHighlight = (text: string | undefined) => {
  if (!text) return '';
  return text.replace(/<em>/g, '<span class="bg-yellow-200 dark:bg-blue-500 px-1 rounded">').replace(/<\/em>/g, '</span>');
};

// Identifier object structure from search_entities
// Comes as single-property objects: {"type:type_id": "value"}
// e.g., {"uniprot:3874827": "P0A6M2"}
export type Identifier = Record<string, string>;

export interface SearchResult {
  id: string;
  entity_id?: number;  // The actual entity ID from the database
  type?: string;
  _formatted?: {
    entity_type?: string;        // "Label:entity_id" like "Protein:385235"
    names?: string[];
    synonyms?: string[];
    gene_symbols?: string[];
    descriptions?: string[];
    references?: string[];
    identifiers?: Identifier[];
    sources?: string[];          // "source_name:source_id"
    // CV term fields
    namespace_name?: string;
    definition?: string;
    name?: string;
    [key: string]: unknown;
  };
  // Raw fields (non-formatted)
  entity_type?: string;
  names?: string[];
  synonyms?: string[];
  gene_symbols?: string[];
  descriptions?: string[];
  references?: string[];
  identifiers?: Identifier[];
  sources?: string[];
  complexes?: number[];
  cv_terms?: number[];
  num_interactions?: number;
  // CV term fields
  namespace_name?: string;
  definition?: string;
  name?: string;
  is_annotated?: boolean;
  associated_entity_ids?: string[];
  [key: string]: unknown; // Add index signature for compatibility with DataRow
}

export function ResultCard({ result }: { result: SearchResult }) {
  const type = result.type || "entity";
  const { addEntity, removeEntity, isSelected } = useEntitySelection();

  // Get display name for selection
  const getDisplayName = () => {
    const geneSymbols = result._formatted?.gene_symbols || result.gene_symbols || [];
    const names = result._formatted?.names || result.names || [];
    return geneSymbols[0] || names[0] || `Entity ${result.entity_id || result.id}`;
  };

  const entityId = (result.entity_id ?? result.id)?.toString();
  const selected = entityId ? isSelected(entityId) : false;

  const handleAddToSelection = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!entityId) return;

    if (selected) {
      removeEntity(entityId);
    } else {
      addEntity({
        id: entityId,
        entityId: result.entity_id,
        name: getDisplayName(),
        type: result.entity_type?.split(':')[0] || result.type,
        complexes: result.complexes,
        cv_terms: result.cv_terms,
        references: result.references,
      });
    }
  };

  // Extract data based on type
  const descriptions = result._formatted?.descriptions || result.descriptions || [];
  const names = result._formatted?.names || result.names || [];
  const geneSymbols = result._formatted?.gene_symbols || result.gene_symbols || [];
  const identifiers = result._formatted?.identifiers || result.identifiers || [];
  const synonyms = result._formatted?.synonyms || result.synonyms || [];
  const references = result._formatted?.references || result.references || [];
  const sources = result._formatted?.sources || result.sources || [];
  const complexes = result.complexes || [];
  const cvTerms = result.cv_terms || [];
  const entityType = result._formatted?.entity_type || result.entity_type;
  const namespaceName = result._formatted?.namespace_name || result.namespace_name;
  const definition = result._formatted?.definition || result.definition;

  // Extract entity type label (e.g., "Protein" from "Protein:385235")
  const entityTypeLabel = entityType ? entityType.split(':')[0] : "Entity";

  // Helper function to truncate text to max characters
  const truncateText = (text: string, maxChars: number = 8): string => {
    if (text.length <= maxChars) return text;
    return text.substring(0, maxChars) + '...';
  };

  // Extract gene symbol or determine title
  let title = "";
  let subtitle = "";
  let primaryIdentifier = "";

  if (type === 'entity') {
    // Priority: gene_symbols > names > first identifier value
    const geneSymbol = geneSymbols.length > 0 ? geneSymbols[0] : undefined;
    const name = names.length > 0 ? names[0] : undefined;
    const firstIdentifier = identifiers.length > 0 ? identifiers[0].value : undefined;

    const displayName = geneSymbol || name || firstIdentifier || `Entity ${result.id}`;

    // Truncate the display name to 8 characters
    const truncatedDisplayName = truncateText(displayName);
    const formattedDisplayName = result._formatted ? convertEmToHighlight(truncatedDisplayName) : truncatedDisplayName;

    // If we have a name and it's different from gene symbol, show both
    if (geneSymbol && name && geneSymbol !== name) {
      primaryIdentifier = name;
      const truncatedPrimaryId = truncateText(primaryIdentifier);
      title = `${formattedDisplayName} <span class="text-sm text-muted-foreground">(${result._formatted ? convertEmToHighlight(truncatedPrimaryId) : truncatedPrimaryId})</span>`;
    } else {
      title = formattedDisplayName;
    }

    // Create subtitle from entity type
    subtitle = entityTypeLabel;
  } else if (type === 'cv_term') {
    const displayName = result._formatted?.name || result.name || `Term ${result.id}`;
    const truncatedDisplayName = truncateText(displayName);
    title = result._formatted ? convertEmToHighlight(truncatedDisplayName) : truncatedDisplayName;
    subtitle = namespaceName || "Ontology Term";
    primaryIdentifier = result.id;
  }

  // Stats
  const interactionCount = result.num_interactions || 0;
  const entityCount = result.associated_entity_ids?.length || 0;

  // Convert identifiers to display format (join all identifier values)
  const allIdentifierValues = identifiers.map(id => id.value);

  return (
    <Card className={`flex flex-col hover:shadow-md transition-shadow h-full result-card group relative ${type === 'cv_term' ? 'cursor-pointer' : ''}`}>
      {/* Add to selection button - positioned at bottom center, visible on hover for entities */}
      {type === 'entity' && entityId && (
        <Button
          variant={selected ? "default" : "secondary"}
          size="icon"
          className={`absolute -bottom-3 left-1/2 -translate-x-1/2 z-10 h-6 w-6 rounded-full shadow-md transition-opacity ${
            selected ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
          }`}
          onClick={handleAddToSelection}
          title={selected ? "Remove from selection" : "Add to selection"}
        >
          {selected ? <Check className="h-3 w-3" /> : <Plus className="h-3 w-3" />}
        </Button>
      )}

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

      {((descriptions.length > 0) || definition) && (
        <div className="flex flex-col min-h-0 flex-grow">
          <CardContent className="px-4 overflow-hidden flex-grow min-h-0">
            {/* Description */}
            <ScrollArea className="h-32 w-full">
              <p className="text-sm text-muted-foreground">
                <span dangerouslySetInnerHTML={{ __html: convertEmToHighlight(definition || descriptions[0] || '') }} />
              </p>
            </ScrollArea>
          </CardContent>
        </div>
      )}

      <CardFooter className={`flex flex-col gap-2 shrink-0 ${((descriptions.length > 0) || definition) ? 'border-t' : ''}`}>
        {/* Stats section */}
        <div className="flex items-center justify-between w-full">
          <div className="flex items-center gap-3 text-sm flex-wrap">
            {type === 'entity' && interactionCount > 0 && (
              <div className="flex items-center gap-1.5 text-muted-foreground">
                <Network className="h-4 w-4" />
                <span>{interactionCount}</span>
              </div>
            )}
            {type === 'entity' && complexes.length > 0 && (
              <div className="flex items-center gap-1.5 text-muted-foreground">
                <Shapes className="h-4 w-4" />
                <span>{complexes.length}</span>
              </div>
            )}
            {type === 'entity' && cvTerms.length > 0 && (
              <div className="flex items-center gap-1.5 text-muted-foreground">
                <Tag className="h-4 w-4" />
                <span>{cvTerms.length}</span>
              </div>
            )}
            {type === 'entity' && references.length > 0 && (
              <div className="flex items-center gap-1.5 text-muted-foreground">
                <FileText className="h-4 w-4" />
                <span>{references.length}</span>
              </div>
            )}
            {type === 'entity' && sources.length > 0 && (
              <div className="flex items-center gap-1.5 text-muted-foreground">
                <Database className="h-4 w-4" />
                <span>{sources.length}</span>
              </div>
            )}
            {type === 'cv_term' && entityCount > 0 && (
              <div className="flex items-center gap-1.5 text-muted-foreground">
                <Tag className="h-4 w-4" />
                <span>{entityCount}</span>
              </div>
            )}
          </div>

          {/* Show detailed info on hover */}
          {type === 'entity' && (identifiers.length > 0 || sources.length > 0 || references.length > 0 || complexes.length > 0 || cvTerms.length > 0) && (
            <HoverCard>
              <HoverCardTrigger asChild>
                <div className="text-sm text-muted-foreground hover:text-foreground transition-colors cursor-pointer">
                  <span>Details</span>
                </div>
              </HoverCardTrigger>
              <HoverCardContent className="w-96 max-h-96 overflow-y-auto">
                <div className="space-y-3">
                  {identifiers.length > 0 && (
                    <div>
                      <h4 className="text-sm font-semibold mb-2">Identifiers ({identifiers.length})</h4>
                      <div className="text-sm text-muted-foreground space-y-1">
                        {identifiers.map((id, idx) => {
                          // Identifiers come as single-property objects: {"type:type_id": "value"}
                          let identifierType = 'unknown';
                          let identifierValue = '';

                          if (typeof id === 'object' && id !== null) {
                            // Get the first (and only) key-value pair
                            const entries = Object.entries(id);
                            if (entries.length > 0) {
                              const [fullKey, value] = entries[0];
                              // Extract type from key (e.g., "uniprot:3874827" -> "uniprot")
                              identifierType = fullKey.split(':')[0];
                              identifierValue = value as string;
                            }
                          } else if (typeof id === 'string') {
                            // Fallback: treat as plain string
                            identifierValue = id;
                          }

                          return (
                            <div key={idx}>
                              <span className="font-medium">{identifierType}:</span> <span dangerouslySetInnerHTML={{ __html: convertEmToHighlight(identifierValue) }} />
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                  {sources.length > 0 && (
                    <div>
                      <h4 className="text-sm font-semibold mb-2">Sources ({sources.length})</h4>
                      <div className="text-sm text-muted-foreground">
                        {sources.map((source, idx) => {
                          // Extract source name from "name:id" format
                          const sourceName = source?.split(':')[0] || source;
                          return <div key={idx}>{sourceName}</div>;
                        })}
                      </div>
                    </div>
                  )}
                  {references.length > 0 && (
                    <div>
                      <h4 className="text-sm font-semibold mb-2">References ({references.length})</h4>
                      <div className="text-sm text-muted-foreground">
                        {references.slice(0, 10).map((ref, idx) => (
                          <div key={idx}>{ref}</div>
                        ))}
                        {references.length > 10 && <div className="text-xs italic">...and {references.length - 10} more</div>}
                      </div>
                    </div>
                  )}
                  {complexes.length > 0 && (
                    <div>
                      <h4 className="text-sm font-semibold mb-2">Complexes ({complexes.length})</h4>
                      <div className="text-sm text-muted-foreground">
                        Member of {complexes.length} complex{complexes.length === 1 ? '' : 'es'}
                      </div>
                    </div>
                  )}
                  {cvTerms.length > 0 && (
                    <div>
                      <h4 className="text-sm font-semibold mb-2">CV Terms ({cvTerms.length})</h4>
                      <div className="text-sm text-muted-foreground">
                        Annotated with {cvTerms.length} term{cvTerms.length === 1 ? '' : 's'}
                      </div>
                    </div>
                  )}
                </div>
              </HoverCardContent>
            </HoverCard>
          )}
          {type === 'cv_term' && synonyms.length > 0 && (
            <HoverCard>
              <HoverCardTrigger asChild>
                <div className="text-sm text-muted-foreground hover:text-foreground transition-colors cursor-pointer">
                  <span>{synonyms.length} synonym{synonyms.length === 1 ? '' : 's'}</span>
                </div>
              </HoverCardTrigger>
              <HoverCardContent className="w-80 max-h-96 overflow-y-auto">
                <div className="space-y-3">
                  <div>
                    <h4 className="text-sm font-semibold mb-2">All Synonyms ({synonyms.length})</h4>
                    <p className="text-sm text-muted-foreground">
                      <span dangerouslySetInnerHTML={{ __html: convertEmToHighlight(synonyms.join(', ')) }} />
                    </p>
                  </div>
                </div>
              </HoverCardContent>
            </HoverCard>
          )}
        </div>
      </CardFooter>
    </Card>
  );
}