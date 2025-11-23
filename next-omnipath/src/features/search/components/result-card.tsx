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
import { ScrollArea } from "@/components/ui/scroll-area";
import React, { useMemo } from "react";
import { Network, Tag, Shapes, FileText, Database, Plus, Check, FlaskConical } from "lucide-react";
import { useEntitySelection } from "@/contexts/entity-selection-context";
import { MoleculeStructure } from "./molecule_structure";
import { getEntityNames } from "../api/queries";
import { useEffect, useState } from "react";
import { ArrowRight, ListOrdered } from "lucide-react";
import { useRouter } from "next/navigation";

// Helper function to convert <em> tags to highlighted spans
const convertEmToHighlight = (text: string | undefined) => {
  if (!text) return '';
  return text.replace(/<em>/g, '<span class="bg-yellow-200 dark:bg-blue-500 px-1 rounded">').replace(/<\/em>/g, '</span>');
};

// Helper to detect if entity is a small molecule or lipid (displayed similarly)
const isSmallMolecule = (result: SearchResult): boolean => {
  const entityType = result._formatted?.entity_type || result.entity_type || '';
  const typeLabel = entityType.split(':')[0].toLowerCase();
  return typeLabel === 'smallmolecule' ||
    typeLabel === 'small_molecule' ||
    typeLabel === 'compound' ||
    typeLabel === 'metabolite' ||
    typeLabel === 'drug' ||
    typeLabel === 'lipid' ||
    // Also check if we have molecule-specific data
    !!(result.canonical_smiles || result.formula || result.molecular_weight);
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
  pathways?: number[];
  reactions?: number[];
  num_interactions?: number;
  // CV term fields
  namespace_name?: string;
  definition?: string;
  name?: string;
  is_annotated?: boolean;
  associated_entity_ids?: string[];
  // Small molecule / compound fields
  canonical_smiles?: string;
  formula?: string;
  molecular_weight?: number;
  // Reaction fields
  reactants?: number[];
  products?: number[];
  stoichiometry?: string[]; // "ID:Stoich"
  // Pathway fields
  pathway_steps?: string[]; // "Order:ID"
  [key: string]: unknown; // Add index signature for compatibility with DataRow
}

// Component to display a reaction equation
function ReactionDisplay({ result }: { result: SearchResult }) {
  const [names, setNames] = useState<Record<string, string>>({});

  const reactants = result.reactants || [];
  const products = result.products || [];
  const stoichiometry = result.stoichiometry || [];

  console.log("ReactionDisplay", { id: result.id, reactants, products, stoichiometry });

  useEffect(() => {
    const idsToFetch = [...reactants, ...products].map(String);
    if (idsToFetch.length > 0) {
      getEntityNames(idsToFetch).then(names => {
        console.log("ReactionDisplay fetched names", names);
        setNames(names);
      });
    }
  }, [result]);

  if (reactants.length === 0 && products.length === 0) {
    console.log("ReactionDisplay: No reactants or products, returning null");
    return null;
  }

  // Parse stoichiometry map: ID -> Coefficient
  const stoichMap: Record<string, string> = {};
  stoichiometry.forEach(s => {
    if (!s) return;
    const [id, val] = s.split(':');
    if (id && val) stoichMap[id] = val;
  });

  const formatPart = (id: number) => {
    const sid = String(id);
    const name = names[sid] || `Entity ${id}`;
    const coeff = stoichMap[sid];
    return (
      <span key={id} className="inline-flex items-center">
        {coeff && coeff !== "1" && <span className="font-bold mr-1 text-muted-foreground">{coeff}</span>}
        <span className="hover:underline cursor-help" title={`ID: ${id}`}>{name}</span>
      </span>
    );
  };

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm p-3 bg-muted/30 rounded-md my-2">
      <div className="flex flex-wrap gap-1 items-center">
        {reactants.map((id, i) => (
          <React.Fragment key={id}>
            {i > 0 && <span className="text-muted-foreground">+</span>}
            {formatPart(id)}
          </React.Fragment>
        ))}
      </div>
      <ArrowRight className="h-4 w-4 text-muted-foreground mx-1" />
      <div className="flex flex-wrap gap-1 items-center">
        {products.map((id, i) => (
          <React.Fragment key={id}>
            {i > 0 && <span className="text-muted-foreground">+</span>}
            {formatPart(id)}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

// Component to display pathway steps
function PathwayDisplay({ result }: { result: SearchResult }) {
  const [names, setNames] = useState<Record<string, string>>({});
  const steps = result.pathway_steps || [];

  useEffect(() => {
    const idsToFetch = steps
      .filter(Boolean)
      .map(s => s.split(':')[1])
      .filter(Boolean);

    if (idsToFetch.length > 0) {
      getEntityNames(idsToFetch).then(setNames);
    }
  }, [result]);

  if (steps.length === 0) return null;

  // Parse and sort steps
  const parsedSteps = steps
    .filter(Boolean) // Filter out null/undefined strings
    .map(s => {
      const [order, id] = s.split(':');
      return { order: parseInt(order), id };
    })
    .sort((a, b) => a.order - b.order);

  return (
    <div className="mt-2">
      <h4 className="text-xs font-semibold uppercase text-muted-foreground mb-1 flex items-center gap-1">
        <ListOrdered className="h-3 w-3" /> Pathway Steps
      </h4>
      <ScrollArea className="h-32 w-full rounded-md border bg-muted/30 p-2">
        <ul className="space-y-1 text-sm">
          {parsedSteps.map((step, i) => (
            <li key={`${step.id}-${i}`} className="flex gap-2">
              <span className="text-muted-foreground w-6 text-right shrink-0">{step.order}.</span>
              <span>{names[step.id] || `Entity ${step.id}`}</span>
            </li>
          ))}
        </ul>
      </ScrollArea>
    </div>
  );
}

// Molecule-specific result card
function MoleculeResultCard({ result }: { result: SearchResult }) {
  const { addEntity, removeEntity, isSelected } = useEntitySelection();

  const names = result._formatted?.names || result.names || [];
  const identifiers = result._formatted?.identifiers || result.identifiers || [];
  const entityType = result._formatted?.entity_type || result.entity_type;
  const entityTypeLabel = entityType ? entityType.split(':')[0] : "Small Molecule";

  // Get primary name from names or identifiers, prefer the shortest meaningful name
  const primaryName = useMemo(() => {
    const validNames: string[] = [];

    // Collect valid names (skip ID-like names)
    for (const name of names) {
      if (!/^(MLS|SMR|cid_|ZINC|SID_|CID_)/i.test(name) && name.length > 3) {
        validNames.push(name);
      }
    }

    // Try to find names from identifiers
    for (const id of identifiers) {
      const entries = Object.entries(id);
      if (entries.length > 0) {
        const [key, value] = entries[0];
        const idType = key.split(':')[0].toLowerCase();
        if (['name', 'common_name', 'preferred_name'].includes(idType) && typeof value === 'string') {
          validNames.push(value);
        }
      }
    }

    // Return the shortest valid name
    if (validNames.length > 0) {
      return validNames.reduce((shortest, current) =>
        current.length < shortest.length ? current : shortest
      );
    }

    // Fallback to first name if all look like IDs
    if (names.length > 0) {
      return names[0];
    }

    return `Compound ${result.entity_id || result.id}`;
  }, [names, identifiers, result.entity_id, result.id]);

  // Extract SMILES from identifiers (stored in "biotin tag" identifier type)
  const smiles = useMemo(() => {
    for (const id of identifiers) {
      const entries = Object.entries(id);
      if (entries.length > 0) {
        const [key, value] = entries[0];
        const idType = key.split(':')[0].toLowerCase().trim();
        if (idType === 'biotin tag' || idType === 'biotin' || idType === 'smiles' || idType === 'canonical_smiles') {
          return value as string;
        }
      }
    }
    return result.canonical_smiles || null;
  }, [identifiers, result.canonical_smiles]);

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
        name: primaryName,
        type: entityTypeLabel,
        complexes: result.complexes,
        cv_terms: result.cv_terms,
        pathways: result.pathways,
        reactions: result.reactions,
        references: result.references,
        fullResult: result,
      });
    }
  };

  return (
    <Card className="flex flex-col hover:shadow-md transition-shadow h-full result-card group relative">
      {/* Add to selection button */}
      {entityId && (
        <Button
          variant={selected ? "default" : "secondary"}
          size="icon"
          className={`absolute -bottom-3 left-1/2 -translate-x-1/2 z-10 h-6 w-6 rounded-full shadow-md transition-opacity ${selected ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
            }`}
          onClick={handleAddToSelection}
          title={selected ? "Remove from selection" : "Add to selection"}
        >
          {selected ? <Check className="h-3 w-3" /> : <Plus className="h-3 w-3" />}
        </Button>
      )}

      <CardHeader className="relative space-y-0 p-3 border-b shrink-0">
        <CardTitle className="text-base line-clamp-3">
          <span dangerouslySetInnerHTML={{ __html: convertEmToHighlight(primaryName) }} />
        </CardTitle>
      </CardHeader>

      {/* Molecule structure visualization */}
      {smiles && (
        <CardContent className="p-3 flex-grow flex flex-col items-center">
          <MoleculeStructure
            smiles={smiles}
            width={180}
            height={140}
            compoundName={primaryName}
            className="rounded-md"
          />
        </CardContent>
      )}

      <CardFooter className="flex items-center justify-between shrink-0 border-t p-2.5">
        {/* Stats */}
        <div className="flex items-center gap-3 text-sm flex-wrap">
          {result.num_interactions && result.num_interactions > 0 && (
            <div className="flex items-center gap-1.5 text-muted-foreground">
              <Network className="h-4 w-4" />
              <span>{result.num_interactions}</span>
            </div>
          )}
          {result.complexes && result.complexes.length > 0 && (
            <div className="flex items-center gap-1.5 text-muted-foreground">
              <Shapes className="h-4 w-4" />
              <span>{result.complexes.length}</span>
            </div>
          )}
          {result.cv_terms && result.cv_terms.length > 0 && (
            <div className="flex items-center gap-1.5 text-muted-foreground">
              <Tag className="h-4 w-4" />
              <span>{result.cv_terms.length}</span>
            </div>
          )}
          {result.references && result.references.length > 0 && (
            <div className="flex items-center gap-1.5 text-muted-foreground">
              <FileText className="h-4 w-4" />
              <span>{result.references.length}</span>
            </div>
          )}
          {result.sources && result.sources.length > 0 && (
            <div className="flex items-center gap-1.5 text-muted-foreground">
              <Database className="h-4 w-4" />
              <span>{result.sources.length}</span>
            </div>
          )}
        </div>
        {/* Badge */}
        <Badge variant="secondary" className="flex items-center gap-1 text-xs">
          <FlaskConical className="h-3 w-3" />
          {entityTypeLabel}
        </Badge>
      </CardFooter>
    </Card>
  );
}

export function ResultCard({ result }: { result: SearchResult }) {
  // Check if this is a small molecule and render specialized card
  if (isSmallMolecule(result)) {
    return <MoleculeResultCard result={result} />;
  }

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
        pathways: result.pathways,
        reactions: result.reactions,
        references: result.references,
        fullResult: result,
      });
    }
  };

  // New handler for clicking the whole card to navigate to explore page
  const router = useRouter();
  const handleCardClick = (e: React.MouseEvent) => {
    // Prevent default link behavior if any
    e.preventDefault();
    if (!entityId) return;
    // Add to selection if not already selected
    if (!selected) {
      addEntity({
        id: entityId,
        entityId: result.entity_id,
        name: getDisplayName(),
        type: result.entity_type?.split(':')[0] || result.type,
        complexes: result.complexes,
        cv_terms: result.cv_terms,
        pathways: result.pathways,
        reactions: result.reactions,
        references: result.references,
        fullResult: result,
      });
    }
    // Navigate to explore page with entity filter
    router.push(`/explore?entity=${entityId}`);
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

  console.log("ResultCard", { id: result.id, entityTypeLabel, reactants: result.reactants });

  // Helper function to truncate text to max characters
  const truncateText = (text: string, maxChars: number = 100): string => {
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
    <Card className={`flex flex-col hover:shadow-md transition-shadow h-full result-card group relative ${type === 'cv_term' ? 'cursor-pointer' : ''}`} onClick={handleCardClick}>
      {/* Add to selection button - positioned at bottom center, visible on hover for entities */}
      {type === 'entity' && entityId && (
        <Button
          variant={selected ? "default" : "secondary"}
          size="icon"
          className={`absolute -bottom-3 left-1/2 -translate-x-1/2 z-10 h-6 w-6 rounded-full shadow-md transition-opacity ${selected ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
            }`}
          onClick={handleAddToSelection}
          title={selected ? "Remove from selection" : "Add to selection"}
        >
          {selected ? <Check className="h-3 w-3" /> : <Plus className="h-3 w-3" />}
        </Button>
      )}

      <CardHeader className="relative space-y-0 p-3 border-b shrink-0">
        <CardTitle className="text-lg line-clamp-3">
          <span dangerouslySetInnerHTML={{ __html: title }} />
        </CardTitle>
      </CardHeader>

      {/* Show content section if there's a description, definition, reaction, or pathway */}
      {((descriptions.length > 0) || definition ||
        entityTypeLabel.toLowerCase() === 'reaction' ||
        entityTypeLabel.toLowerCase() === 'pathway') && (
          <div className="flex flex-col min-h-0 flex-grow">
            <CardContent className="px-4 overflow-hidden flex-grow min-h-0">
              {/* Description */}
              {((descriptions.length > 0) || definition) && (
                <ScrollArea className="h-24 w-full mb-2">
                  <p className="text-sm text-muted-foreground">
                    <span dangerouslySetInnerHTML={{ __html: convertEmToHighlight(definition || descriptions[0] || '') }} />
                  </p>
                </ScrollArea>
              )}

              {/* Reaction Equation */}
              {entityTypeLabel.toLowerCase() === 'reaction' && (
                <ReactionDisplay result={result} />
              )}

              {/* Pathway Steps */}
              {entityTypeLabel.toLowerCase() === 'pathway' && (
                <PathwayDisplay result={result} />
              )}
            </CardContent>
          </div>
        )}

      <CardFooter className={`flex items-center justify-between shrink-0 p-2.5 ${((descriptions.length > 0) || definition) ? 'border-t' : ''}`}>
        {/* Stats */}
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
          {type === 'cv_term' && synonyms.length > 0 && (
            <div className="flex items-center gap-1.5 text-muted-foreground text-xs">
              {synonyms.length} synonym{synonyms.length === 1 ? '' : 's'}
            </div>
          )}
        </div>
        {/* Badge */}
        <Badge variant="secondary" className="text-xs">
          {subtitle}
        </Badge>
      </CardFooter>
    </Card>
  );
}