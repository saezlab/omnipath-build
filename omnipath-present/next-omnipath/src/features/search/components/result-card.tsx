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
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { ScrollArea } from "@/components/ui/scroll-area";
import React, { useMemo, useState } from "react";
import { Network, Tag, Shapes, FileText, Database, Plus, Check, FlaskConical, ArrowRight, ListOrdered, ChevronDown, ChevronUp, Copy, Loader2 } from "lucide-react";
import { useEntitySelection } from "@/contexts/entity-selection-context";
import { MoleculeStructure } from "./molecule_structure";
import { searchMeilisearch } from "@/lib/meilisearch/search";
import { INDEXES } from "@/lib/meilisearch/client";

// Component that shows a ResultCardContent in a HoverCard for entities
export function EntityHoverCard({
  entityId,
  children
}: {
  entityId: string;
  children: React.ReactNode;
}) {
  const [entity, setEntity] = useState<SearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [hasLoaded, setHasLoaded] = useState(false);

  const handleOpenChange = async (open: boolean) => {
    if (open && !hasLoaded) {
      setLoading(true);
      try {
        const result = await searchMeilisearch({
          query: '',
          index: INDEXES.ENTITIES,
          limit: 1,
          filters: { entity_ids: [parseInt(entityId)] }
        });
        if (result.hits.length > 0) {
          setEntity(result.hits[0] as SearchResult);
        }
      } catch (error) {
        console.error('Failed to fetch entity:', error);
      } finally {
        setLoading(false);
        setHasLoaded(true);
      }
    }
  };

  return (
    <HoverCard openDelay={300} closeDelay={100} onOpenChange={handleOpenChange}>
      <HoverCardTrigger asChild>
        {children}
      </HoverCardTrigger>
      <HoverCardContent side="right" align="start" className="w-80 p-0">
        {loading ? (
          <div className="flex items-center justify-center p-4">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : entity ? (
          <ResultCardContent result={entity} />
        ) : (
          <div className="p-4 text-sm text-muted-foreground">
            No details available
          </div>
        )}
      </HoverCardContent>
    </HoverCard>
  );
}

// Component that shows a ResultCardContent in a HoverCard for CV terms
export function CvTermHoverCard({
  termId,
  children
}: {
  termId: string;
  children: React.ReactNode;
}) {
  const [term, setTerm] = useState<SearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [hasLoaded, setHasLoaded] = useState(false);

  const handleOpenChange = async (open: boolean) => {
    if (open && !hasLoaded) {
      setLoading(true);
      try {
        const response = await fetch("/api/ontology/terms", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            termIds: [termId]
          }),
        });

        if (!response.ok) throw new Error("Failed to fetch term");

        const data = await response.json();
        const terms = data.terms || {};
        const termData = terms[termId];

        if (termData) {
          // Map to SearchResult format for compatibility with ResultCardContent
          setTerm({
            id: termData.id,
            type: "cv_term",
            name: termData.name,
            definition: termData.definition,
            namespace_name: termData.namespace,
          } as SearchResult);
        }
      } catch (error) {
        console.error('Failed to fetch CV term:', error);
      } finally {
        setLoading(false);
        setHasLoaded(true);
      }
    }
  };

  return (
    <HoverCard openDelay={300} closeDelay={100} onOpenChange={handleOpenChange}>
      <HoverCardTrigger asChild>
        {children}
      </HoverCardTrigger>
      <HoverCardContent side="right" align="start" className="w-80 p-0">
        {loading ? (
          <div className="flex items-center justify-center p-4">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : term ? (
          <ResultCardContent result={term} />
        ) : (
          <div className="p-4 text-sm text-muted-foreground">
            No details available
          </div>
        )}
      </HoverCardContent>
    </HoverCard>
  );
}

// Helper function to convert <em> tags to highlighted spans
const convertEmToHighlight = (text: string | undefined) => {
  if (!text) return '';
  return text.replace(/<em>/g, '<span class="bg-yellow-200 dark:bg-blue-500 px-1 rounded">').replace(/<\/em>/g, '</span>');
};

// Helper to detect if entity is a small molecule or lipid (displayed similarly)
const isSmallMolecule = (result: SearchResult): boolean => {
  const entityType = result._formatted?.entity_type || result.entity_type || '';
  // Extract type label from "Label:Accession" format and normalize (remove spaces/underscores)
  const typeLabel = entityType.split(':')[0].toLowerCase().replace(/[\s_]/g, '');
  return typeLabel === 'smallmolecule' ||
    typeLabel === 'compound' ||
    typeLabel === 'metabolite' ||
    typeLabel === 'drug' ||
    typeLabel === 'lipid' ||
    // Also check if we have molecule-specific data
    !!(result.canonical_smiles || result.formula || result.molecular_weight);
};

// Identifier object structure from search_entities
// New format: {key: "type:accession", value: "identifier_value"}
// e.g., {key: "uniprot:OM:0001", value: "P0A6M2"}
export type Identifier = { key: string; value: string } | Record<string, string>;

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
  cv_terms?: string[];
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

// Single identifier badge with copy functionality
function IdentifierBadge({ id, idx }: { id: { type: string; value: string }; idx: number }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(id.value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <span
      key={`${id.type}-${idx}`}
      className="group/id inline-flex items-center gap-1 bg-background/80 border rounded px-1.5 py-0.5 hover:bg-background"
      title={`${id.type}: ${id.value}`}
    >
      <span className="text-muted-foreground font-medium">{id.type}:</span>
      <span className="font-mono truncate max-w-[120px]">{id.value}</span>
      <button
        onClick={handleCopy}
        className="opacity-0 group-hover/id:opacity-100 transition-opacity p-0.5 hover:bg-muted rounded"
        title="Copy to clipboard"
      >
        {copied ? (
          <Check className="h-3 w-3 text-green-500" />
        ) : (
          <Copy className="h-3 w-3 text-muted-foreground" />
        )}
      </button>
    </span>
  );
}

// Component to display identifiers in a collapsible section
function IdentifiersDisplay({ identifiers }: { identifiers: Identifier[] }) {
  const [isExpanded, setIsExpanded] = useState(false);

  if (!identifiers || identifiers.length === 0) return null;

  const parsedIdentifiers = identifiers.map(id => {
    // Fallback for old format: {"type": "value"}
    const entries = Object.entries(id);
    if (entries.length === 0) return null;
    const [key, value] = entries[0];
    const colonIndex = key.indexOf(':');
    const idType = colonIndex > 0 ? key.substring(0, colonIndex) : key;
    return { type: idType, value: value as string };
  }).filter(Boolean) as { type: string; value: string }[];

  if (parsedIdentifiers.length === 0) return null;

  // Show first 3 when collapsed
  const displayedIdentifiers = isExpanded ? parsedIdentifiers : parsedIdentifiers.slice(0, 3);
  const hasMore = parsedIdentifiers.length > 3;

  return (
    <div className="border-t px-3 py-2 bg-muted/30">
      <div className="flex flex-wrap gap-1.5 text-xs">
        {displayedIdentifiers.map((id, idx) => (
          <IdentifierBadge key={`${id.type}-${idx}`} id={id} idx={idx} />
        ))}
        {hasMore && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              setIsExpanded(!isExpanded);
            }}
            className="inline-flex items-center gap-0.5 text-muted-foreground hover:text-foreground transition-colors px-1"
          >
            {isExpanded ? (
              <>
                <ChevronUp className="h-3 w-3" />
                <span>Show less</span>
              </>
            ) : (
              <>
                <ChevronDown className="h-3 w-3" />
                <span>+{parsedIdentifiers.length - 3} more</span>
              </>
            )}
          </button>
        )}
      </div>
    </div>
  );
}


// Component to display a reaction equation
function ReactionDisplay({ result, names = {} }: { result: SearchResult, names?: Record<string, string> }) {
  const reactants = result.reactants || [];
  const products = result.products || [];
  const stoichiometry = result.stoichiometry || [];

  console.log("ReactionDisplay", { id: result.id, reactants, products, stoichiometry });

  if (reactants.length === 0 && products.length === 0) {
    console.log("ReactionDisplay: No reactants or products, returning null");
    return null;
  }

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
function PathwayDisplay({ result, names = {} }: { result: SearchResult, names?: Record<string, string> }) {
  const steps = result.pathway_steps || [];

  if (steps.length === 0) return null;

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
  const entityType = result._formatted?.entity_type || result.entity_type;
  const entityTypeLabel = entityType ? entityType.split(':')[0] : "Small Molecule";

  // Memoize identifiers for stable reference in JSX
  const identifiers = useMemo(() =>
    result._formatted?.identifiers || result.identifiers || [],
    [result._formatted?.identifiers, result.identifiers]
  );

  // Get primary name from names or identifiers, prefer the shortest meaningful name
  const primaryName = useMemo(() => {
    const names = result._formatted?.names || result.names || [];
    const identifiers = result._formatted?.identifiers || result.identifiers || [];
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
  }, [result._formatted?.names, result.names, result._formatted?.identifiers, result.identifiers, result.entity_id, result.id]);

  // Extract SMILES from identifiers (stored in "biotin tag" identifier type)
  const smiles = useMemo(() => {
    const identifiers = result._formatted?.identifiers || result.identifiers || [];
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
  }, [result._formatted?.identifiers, result.identifiers, result.canonical_smiles]);

  const { addEntity, removeEntity, isSelected } = useEntitySelection();
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
        cv_terms: result.cv_terms,
        references: result.references,
        fullResult: result,
      });
    }
  };

  const handleCardClick = (e: React.MouseEvent) => {
    e.preventDefault();
    if (!entityId) return;
    if (selected) {
      removeEntity(entityId);
      return;
    }
    addEntity({
      id: entityId,
      entityId: result.entity_id,
      name: primaryName,
      type: entityTypeLabel,
      cv_terms: result.cv_terms,
      references: result.references,
      fullResult: result,
    });
  };

  return (
    <Card
      className="flex flex-col hover:shadow-md transition-shadow h-full result-card group relative cursor-pointer"
      onClick={handleCardClick}
    >
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

      {/* Identifiers section */}
      <IdentifiersDisplay identifiers={identifiers} />

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

export function ResultCard({ result, entityNamesMap }: { result: SearchResult, entityNamesMap?: Record<string, string> }) {
  const { addEntity, removeEntity, isSelected } = useEntitySelection();
  const type = result.type || "entity";

  // Check if this is a small molecule and render specialized card
  if (isSmallMolecule(result)) {
    return <MoleculeResultCard result={result} />;
  }

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
        cv_terms: result.cv_terms,
        references: result.references,
        fullResult: result,
      });
    }
  };

  // New handler for clicking the whole card to toggle selection
  const handleCardClick = (e: React.MouseEvent) => {
    e.preventDefault();
    if (type !== "entity" || !entityId) return;
    if (selected) {
      removeEntity(entityId);
      return;
    }
    addEntity({
      id: entityId,
      entityId: result.entity_id,
      name: getDisplayName(),
      type: result.entity_type?.split(':')[0] || result.type,
      cv_terms: result.cv_terms,
      references: result.references,
      fullResult: result,
    });
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



  return (
    <Card className={`flex flex-col hover:shadow-md transition-shadow h-full result-card group relative ${type === 'entity' ? 'cursor-pointer' : ''}`} onClick={handleCardClick}>
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
                <ReactionDisplay result={result} names={entityNamesMap} />
              )}

              {/* Pathway Steps */}
              {entityTypeLabel.toLowerCase() === 'pathway' && (
                <PathwayDisplay result={result} names={entityNamesMap} />
              )}
            </CardContent>
          </div>
        )}

      {/* Identifiers section */}
      {type === 'entity' && <IdentifiersDisplay identifiers={identifiers} />}

      <CardFooter className={`flex items-center justify-between shrink-0 p-2.5 ${((descriptions.length > 0) || definition || identifiers.length > 0) ? 'border-t' : ''}`}>
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

/**
 * Compact content version of ResultCard for use in hover cards.
 * Displays title, definition/description, and entity type without Card wrapper.
 */
export function ResultCardContent({ result }: { result: SearchResult }) {
  const type = result.type || "entity";

  // Extract data
  const descriptions = result._formatted?.descriptions || result.descriptions || [];
  const names = result._formatted?.names || result.names || [];
  const geneSymbols = result._formatted?.gene_symbols || result.gene_symbols || [];
  const entityType = result._formatted?.entity_type || result.entity_type;
  const namespaceName = result._formatted?.namespace_name || result.namespace_name;
  const definition = result._formatted?.definition || result.definition;

  const entityTypeLabel = entityType ? entityType.split(':')[0] : "Entity";

  // Determine title
  let title = "";
  let subtitle = "";

  if (type === 'entity' || type === 'cv_term') {
    const geneSymbol = geneSymbols.length > 0 ? geneSymbols[0] : undefined;
    const name = names.length > 0 ? names[0] : undefined;
    const displayName = result._formatted?.name || result.name || geneSymbol || name || `Entity ${result.entity_id || result.id}`;
    title = displayName;
    subtitle = namespaceName || entityTypeLabel;
  }

  const descriptionText = definition || descriptions[0] || '';

  return (
    <div className="space-y-2 p-3">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <h4 className="font-semibold text-sm leading-tight line-clamp-2">
          {title}
        </h4>
        <Badge variant="secondary" className="text-xs flex-shrink-0">
          {subtitle}
        </Badge>
      </div>

      {/* Definition/Description */}
      {descriptionText && (
        <div className="h-24 overflow-y-auto">
          <p className="text-xs text-muted-foreground pr-2">
            {descriptionText}
          </p>
        </div>
      )}
    </div>
  );
}
