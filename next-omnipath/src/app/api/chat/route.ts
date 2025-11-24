import { google } from "@/ai";
import { convertToCoreMessages, smoothStream, streamText } from "ai";
import { z } from "zod";
import {
  searchMeilisearch,
  searchInteractionsMeilisearch
} from "@/lib/meilisearch/search";
import type { MeilisearchFilters } from "@/types/meilisearch";
import { INDEXES } from "@/lib/meilisearch/client";


// Define types for Meilisearch hits
interface EntityHit {
  id: string | number;
  canonical_identifier?: string;
  display_name?: string;
  gene_symbol?: string;
  description?: string;
  entity_type?: { name?: string } | string;
  interaction_ids?: unknown[];
  [key: string]: unknown;
}

interface CVTermHit {
  id: string | number;
  name?: string;
  definition?: string;
  namespace?: { name?: string } | string;
  associated_entity_ids?: unknown[];
  [key: string]: unknown;
}

interface InteractionHit {
  id: string | number;
  entity_a_name?: string;
  entity_a_canonical_id?: string;
  entity_b_name?: string;
  entity_b_canonical_id?: string;
  interaction_types?: Array<{ name?: string }>;
  evidence_count?: number;
  [key: string]: unknown;
}

// Define the message schema
const messageSchema = z.object({
  role: z.enum(["user", "assistant", "system"]),
  content: z.string(),
});

// Define the request schema
const requestSchema = z.object({
  messages: z.array(messageSchema),
});

// Define the tools
const tools = {
  searchEntities: {
    description: `Search for biological entities (proteins, genes, complexes) or controlled vocabulary terms using Meilisearch.
This is a fast full-text search that can find entities by name, identifier, or description.

INDEX SELECTION GUIDE:
- Use 'entities' for: protein names, gene symbols, complexes, UniProt IDs, or when user asks about proteins/genes
- Use 'cv_terms' for: ontology terms (GO, DO, HP, etc.), biological processes, molecular functions, cellular components, diseases, phenotypes

Examples:
- "insulin" → entities (protein/gene)
- "P53" or "TP53" → entities (protein/gene symbol)
- "kinase" → entities (protein family)
- "apoptosis" → cv_terms (biological process)
- "GO:0006915" → cv_terms (GO term ID)
- "cancer" → cv_terms (disease term)
- "mitochondria" → cv_terms (cellular component)`,
    parameters: z.object({
      query: z.string().describe("The search query (protein name, gene symbol, identifier, or description)"),
      searchType: z.enum(["entities", "cv_terms"]).default("entities").describe("What to search for: 'entities' (proteins/genes/complexes) or 'cv_terms' (controlled vocabulary terms like GO, DO, etc.)"),
      limit: z.number().min(1).max(100).default(20).describe("Maximum number of results to return (1-100)"),
    }),
    execute: async ({ query, searchType, limit }: { query: string; searchType: "entities" | "cv_terms"; limit: number }) => {
      console.log(`Searching ${searchType} for: ${query}`);
      try {
        // Use the common ENTITIES index for both types
        // We could add filters here if needed to distinguish between entities and cv_terms
        // but for now we'll rely on the query matching relevant documents
        const data = await searchMeilisearch({
          query,
          index: INDEXES.ENTITIES,
          limit,
          offset: 0,
        });

        const hits = (data.hits || []) as (EntityHit | CVTermHit)[];
        console.log(`Search returned ${hits.length} results.`);
        console.log('Sample hit for preview:', JSON.stringify(hits[0], null, 2));

        // AI intelligently selects the best match
        // Since Meilisearch returns results ordered by relevance, we can use scoring heuristics
        let bestMatchId: string | number | undefined = undefined;

        if (hits.length > 0) {
          const queryLower = query.toLowerCase();
          let bestScore = -1;
          let bestHit: EntityHit | CVTermHit | null = null;

          // Score each hit to find the best match
          for (let i = 0; i < Math.min(hits.length, 5); i++) { // Check top 5 results
            const hit = hits[i];
            let score = 5 - i; // Base score from position (5 for first, 4 for second, etc.)

            if (searchType === "entities") {
              const entityHit = hit as EntityHit;
              const displayName = (entityHit.display_name || "").toLowerCase();
              const geneSymbol = (entityHit.gene_symbol || "").toLowerCase();
              const description = (entityHit.description || "").toLowerCase();

              // Exact matches get highest score
              if (displayName === queryLower || geneSymbol === queryLower) {
                score += 100;
              }
              // Starts with query gets high score
              else if (displayName.startsWith(queryLower) || geneSymbol.startsWith(queryLower)) {
                score += 50;
              }
              // Contains query gets moderate score
              else if (displayName.includes(queryLower) || geneSymbol.includes(queryLower)) {
                score += 25;
              }

              // Bonus for popular/well-annotated entities
              if (entityHit.interaction_ids && entityHit.interaction_ids.length > 100) {
                score += 10;
              }
              if (description && description.length > 100) {
                score += 5;
              }
            } else {
              // CV terms
              const cvHit = hit as CVTermHit;
              const name = (cvHit.name || "").toLowerCase();
              const definition = (cvHit.definition || "").toLowerCase();

              // Exact match
              if (name === queryLower) {
                score += 100;
              }
              // Starts with query
              else if (name.startsWith(queryLower)) {
                score += 50;
              }
              // Contains query
              else if (name.includes(queryLower)) {
                score += 25;
              }

              // Bonus for well-annotated terms
              if (cvHit.associated_entity_ids && cvHit.associated_entity_ids.length > 10) {
                score += 10;
              }
              if (definition && definition.length > 50) {
                score += 5;
              }
            }

            if (score > bestScore) {
              bestScore = score;
              bestHit = hit;
            }
          }

          // Always select the best scored hit as the AI's choice
          if (bestHit) {
            bestMatchId = searchType === "entities"
              ? ((bestHit as EntityHit).canonical_identifier || bestHit.id)
              : bestHit.id;
          }
        }

        // Return minimal data with component parameters
        // Include top 3 results as preview for AI context
        const preview = hits.slice(0, 3).map((hit: EntityHit | CVTermHit) => ({
          id: hit.id,
          name: searchType === "entities" ? ((hit as EntityHit).display_name || (hit as EntityHit).gene_symbol || (hit as EntityHit).canonical_identifier || `Entity ${hit.id}`) : ((hit as CVTermHit).name || `Term ${hit.id}`),
          type: searchType === "entities" ? (typeof (hit as EntityHit).entity_type === 'object' ? ((hit as EntityHit).entity_type as { name?: string })?.name : (hit as EntityHit).entity_type as string) || 'protein' : (typeof (hit as CVTermHit).namespace === 'object' ? ((hit as CVTermHit).namespace as { name?: string })?.name : (hit as CVTermHit).namespace as string) || 'term',
          ...(searchType === "entities" && {
            canonical_identifier: (hit as EntityHit).canonical_identifier,
            interaction_count: (hit as EntityHit).interaction_ids?.length || 0
          }),
          ...(searchType === "cv_terms" && {
            associated_entities: (hit as CVTermHit).associated_entity_ids?.length || 0
          })
        }));

        return {
          // Component parameters for dialog
          componentParams: {
            searchType,
            query,
            limit,
            bestMatchId
          },
          // Minimal preview data for AI context
          preview,
          stats: {
            totalCount: data.estimatedTotalHits || hits.length,
            hasMore: hits.length < (typeof data.estimatedTotalHits === 'number' ? data.estimatedTotalHits : hits.length)
          },
          // For backward compatibility
          results: [],
          totalCount: data.estimatedTotalHits || hits.length,
          searchType,
          query,
          bestMatchId,
        };
      } catch (error: unknown) {
        console.error("Error searching entities:", error);
        return { error: error instanceof Error ? error.message : 'Unknown search error' };
      }
    },
  },

  searchInteractions: {
    description: `Search for molecular interactions.
IMPORTANT: The interactions index CANNOT search by entity names directly - it only supports filtering by entity IDs.
To find interactions for a specific protein/gene:
1. First use searchEntities to find the entity and get its ID
2. Then use this tool with the entity ID`,
    parameters: z.object({
      entityIds: z.array(z.string()).optional().describe("Entity IDs to filter interactions by. Use searchEntities first to get these IDs."),
      limit: z.number().min(1).max(100).default(20).describe("Maximum number of results to return (1-100)"),
    }),
    execute: async ({ entityIds, limit }: {
      entityIds?: string[];
      limit: number;
    }) => {
      console.log(`Searching interactions with entity IDs: ${entityIds?.join(', ') || 'none'}`);
      try {
        // Build the request with filters
        const apiFilters: MeilisearchFilters = {};

        // Add entity IDs filter if provided
        if (entityIds && entityIds.length > 0) {
          // Convert string IDs to numbers as required by MeilisearchFilters
          apiFilters.entity_ids = entityIds.map(id => parseInt(id, 10)).filter(id => !isNaN(id));
        }

        const requestParams = {
          query: "", // Interactions index doesn't support text search
          limit,
          offset: 0,
          index: INDEXES.INTERACTIONS,
          filters: apiFilters,
        };

        const data = await searchInteractionsMeilisearch(requestParams);

        const hits = (data.hits || []) as InteractionHit[];
        console.log(`Interaction search returned ${hits.length} results.`);

        // Extract and format facet statistics for AI analysis
        const facetStats = (data.facetDistribution || {}) as Record<string, Record<string, number>>;

        // Format facet data for better AI understanding
        const formattedFacets = {
          interactionTypes: facetStats['interaction_types_facet'] || {},
          dataSources: facetStats['data_sources_facet'] || {},
          detectionMethods: facetStats['detection_methods_facet'] || {},
          causalStatements: facetStats['causal_statements_facet'] || {},
          causalMechanisms: facetStats['causal_mechanisms_facet'] || {},
          interactorTypes: facetStats['interactor_types_facet'] || {},
          signs: facetStats['signs'] || {},
          consensusSign: facetStats['consensus_sign'] || {},
          isDirected: facetStats['is_directed'] || {},
          consensusDirection: facetStats['consensus_direction'] || {},
          evidenceCountDistribution: facetStats['evidence_count'] || {}
        };

        // Calculate summary statistics from facets
        const summaryStats = {
          totalInteractions: data.estimatedTotalHits || hits.length,
          uniqueInteractionTypes: Object.keys(formattedFacets.interactionTypes).length,
          uniqueDataSources: Object.keys(formattedFacets.dataSources).length,
          uniqueDetectionMethods: Object.keys(formattedFacets.detectionMethods).length,
          directedInteractions: formattedFacets.isDirected['true'] || 0,
          undirectedInteractions: formattedFacets.isDirected['false'] || 0,
          // Top categories
          topInteractionTypes: Object.entries(formattedFacets.interactionTypes)
            .sort(([, a], [, b]) => (b as number) - (a as number))
            .slice(0, 5)
            .map(([name, count]) => ({ name, count })),
          topDataSources: Object.entries(formattedFacets.dataSources)
            .sort(([, a], [, b]) => (b as number) - (a as number))
            .slice(0, 5)
            .map(([name, count]) => ({ name, count })),
          topDetectionMethods: Object.entries(formattedFacets.detectionMethods)
            .sort(([, a], [, b]) => (b as number) - (a as number))
            .slice(0, 5)
            .map(([name, count]) => ({ name, count }))
        };

        return {
          // Component parameters for dialog
          componentParams: {
            entityIds,
            limit
          },
          // Focus on facet statistics for AI analysis
          facetStatistics: formattedFacets,
          summary: summaryStats,
          // Minimal interaction preview (just 1-2 examples)
          exampleInteractions: hits.slice(0, 2).map((hit: InteractionHit) => ({
            participants: `${hit.entity_a_name || hit.entity_a_canonical_id} - ${hit.entity_b_name || hit.entity_b_canonical_id}`,
            type: hit.interaction_types?.[0]?.name || 'interaction',
            evidences: hit.evidence_count || 0
          })),
          // For backward compatibility
          totalCount: data.estimatedTotalHits || hits.length,
          entityIds,
        };
      } catch (error: unknown) {
        console.error("Error searching interactions:", error);
        return { error: error instanceof Error ? error.message : 'Unknown search error' };
      }
    },
  },
};



export async function POST(req: Request) {
  try {
    const body = await req.json();
    const { messages } = requestSchema.parse(body);

    // Convert messages to core format
    const coreMessages = convertToCoreMessages(messages).filter(
      (message) => message.content.length > 0,
    );

    // Add system message if not present
    if (!coreMessages.some(m => m.role === "system")) {
      coreMessages.unshift({
        role: "system",
        content: `You are OmniPath AI, a helpful assistant knowledgeable about molecular interactions, pathways, and biological annotations based on the OmniPath database.

Your capabilities:
- Search for proteins, genes, complexes, and controlled vocabulary terms
- Find molecular interactions and their evidence

Guidelines:
- Use the search tools when users ask about specific proteins, genes, or interactions
- Be concise but informative
- Today's date is ${new Date().toLocaleDateString()}

CRITICAL: Finding interactions for proteins/genes:
When users ask about interactions involving specific proteins or genes (e.g., "Show me p53 interactions", "what interacts with EGFR"):
1. FIRST use searchEntities to find the protein/gene and get its DATABASE ID (the 'id' field, NOT the canonical_identifier)
2. Tell the user: "I found [entity name] ([canonical_identifier]). Let me search for its interactions..."
3. THEN YOU MUST IMMEDIATELY call searchInteractions with the entity's DATABASE ID (the numeric 'id' field from the search results)
4. DO NOT use canonical_identifier (like P00533) for searchInteractions - use the numeric 'id' field
5. This two-step process is REQUIRED because the interactions index cannot search by name

Example flow:
User: "Show me EGFR interactions"
You: [Call searchEntities for "EGFR"]
Result has: {id: "12345", canonical_identifier: "P00533", display_name: "EGFR", ...}
You: "I found EGFR (P00533). Let me search for its interactions..."
You: [Call searchInteractions with entityIds: ["12345"]] // Use the id field, NOT P00533
You: [Display the interaction results]

CRITICAL: When presenting interaction search results:
The searchInteractions tool returns detailed facet statistics that provide a comprehensive overview of the interaction data. You MUST focus on these statistics when describing results:

1. START with the total number of interactions found
2. EMPHASIZE the facet statistics:
   - Mention the NUMBER of unique interaction types, data sources, and detection methods
   - List the TOP 3-5 interaction types with their counts (e.g., "physical association (523), direct interaction (312)")
   - List the TOP 3-5 data sources with their counts
   - Highlight detection methods if relevant
   - Mention the breakdown of directed vs undirected interactions
   - Note any causal information (activation/inhibition) if present

3. Only briefly mention 1-2 example interactions at the end
4. DO NOT list individual interactions unless specifically asked

Example response format:
"I found 847 interactions involving EGFR. Here's an overview of the interaction data:

**Interaction Types:** The interactions span 12 different types, with the most common being:
- Physical association (234 interactions)
- Direct interaction (189 interactions)
- Phosphorylation (98 interactions)
- Activation (67 interactions)
- Binding (45 interactions)

**Data Sources:** These interactions come from 8 different databases:
- BioGRID (312 interactions)
- IntAct (245 interactions)
- SIGNOR (178 interactions)
- STRING (89 interactions)

**Detection Methods:** 15 different experimental methods were used, including affinity chromatography technology (123), two hybrid (89), and anti bait coimmunoprecipitation (67).

**Directionality:** 523 interactions are directed (showing regulatory relationships) while 324 are undirected.

For example, EGFR phosphorylates STAT3 (supported by 12 evidence records) and physically associates with GRB2 (23 evidence records)."

After receiving tool responses:
1. Transform raw data into natural, conversational responses
2. The search results will highlight the best match automatically
3. For entities, mention their function, organism, and interaction count
4. For CV terms, explain their meaning and associated entities
5. Suggest follow-up queries when appropriate
`
      });
    }

    const stream = streamText({
      model: google("gemini-2.5-flash"),
      messages: coreMessages,
      tools,
      toolChoice: "auto",
      experimental_transform: [
        smoothStream({
          chunking: "word",
        }),
      ],
      onFinish: async (result) => {
        // Here you could save the chat history if needed
        console.log("Chat completed with result:", result);
      },
      maxSteps: 5,
      temperature: 0.7,
    });

    return stream.toDataStreamResponse({
      headers: {
        'Transfer-Encoding': 'chunked',
        Connection: 'keep-alive',
      },
      sendReasoning: true,
      getErrorMessage: (error) => {
        console.error("Chat stream error:", error);
        return `An error occurred, please try again!`;
      },
    });

  } catch (error: unknown) {
    console.error("Error in chat endpoint:", error);
    return new Response("Failed to process chat request", { status: 500 });
  }
}
