"use client";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import cytoscape from "cytoscape";
import { useTheme } from "next-themes";
import dynamic from "next/dynamic";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ResultCard } from "@/features/search/components/result-card";
import { fetchMeilisearchDocuments } from "@/lib/meilisearch/search";

interface EntityDocument {
  id: string;
  [key: string]: unknown;
}

// Type for interaction data - matches the format expected by GraphView
interface InteractionData {
  id?: string | number
  entity_a?: {
    id?: string
    canonical_identifier?: string
    display_name?: string
    entity_type?: { name?: string }
  }
  entity_b?: {
    id?: string
    canonical_identifier?: string
    display_name?: string
    entity_type?: { name?: string }
  }
  has_directed_evidence?: boolean
  consensus_sign?: string | null
  evidences?: Array<{ id: number; [key: string]: unknown }>
}

// Types for graph data
interface GraphNode {
  id: string;
  label: string;
  type: string;
  entity: Record<string, unknown>; // Store full entity data for details
  canonicalIdentifier?: string;
}

interface GraphEdge {
  id: string;
  source: string;
  target: string;
  directed: boolean;
  consensusSign: string;
  evidences: Array<{ id: number; [key: string]: unknown }>;
}

interface GraphViewProps {
  interactions: InteractionData[];
  entityId?: string; // Make optional - when provided, this entity is highlighted as central
  selectedInteraction?: InteractionData | null;
  onSelectInteraction?: (interaction: InteractionData) => void;
}

// Fetch entity document from Meilisearch
async function fetchEntityDocument(entityId: string): Promise<EntityDocument | null> {
  try {
    const response = await fetchMeilisearchDocuments('entities', [entityId]);
    const documents = response.documents as unknown[];
    const doc = documents?.[0] as EntityDocument;
    
    if (doc && !doc.id) {
      // Add the entityId as id if missing
      doc.id = entityId;
    }
    return doc || null;
  } catch (error) {
    console.error('Error fetching entity document:', error);
    return null;
  }
}

// Transform interactions data to graph format
const transformToGraphData = (
  interactions: InteractionData[], 
  entityId?: string
) => {
  const nodes = new Map<string, GraphNode>();
  const edges: GraphEdge[] = [];
  const MAX_NODES = 200;

  // If we have a center entity, ensure it's always included
  const centerEntityId = entityId;
  
  // Process interactions, but stop adding new nodes after reaching the limit
  interactions.forEach((interaction) => {
    const sourceId = interaction.entity_a?.id?.toString() || '';
    const targetId = interaction.entity_b?.id?.toString() || '';

    // Check if we've reached the node limit (but always allow the center entity)
    const canAddNode = (nodeId: string) => {
      return nodes.size < MAX_NODES || nodeId === centerEntityId || nodes.has(nodeId);
    };

    // Add source node
    if (sourceId && !nodes.has(sourceId) && canAddNode(sourceId)) {
      nodes.set(sourceId, {
        id: sourceId,
        label: interaction.entity_a?.display_name || 
               interaction.entity_a?.canonical_identifier || 
               sourceId,
        type: interaction.entity_a?.entity_type?.name || 'unknown',
        entity: interaction.entity_a || {},
        canonicalIdentifier: interaction.entity_a?.canonical_identifier
      });
    }

    // Add target node
    if (targetId && !nodes.has(targetId) && canAddNode(targetId)) {
      nodes.set(targetId, {
        id: targetId,
        label: interaction.entity_b?.display_name || 
               interaction.entity_b?.canonical_identifier || 
               targetId,
        type: interaction.entity_b?.entity_type?.name || 'unknown',
        entity: interaction.entity_b || {},
        canonicalIdentifier: interaction.entity_b?.canonical_identifier
      });
    }

    // Add edge only if both nodes exist
    if (sourceId && targetId && sourceId !== targetId && nodes.has(sourceId) && nodes.has(targetId)) {
      edges.push({
        id: `${interaction.id}`,
        source: sourceId,
        target: targetId,
        directed: interaction.has_directed_evidence || false,
        consensusSign: interaction.consensus_sign || 'unknown',
        evidences: interaction.evidences || []
      });
    }
  });

  // Count how many unique nodes we would have had without the limit
  const totalUniqueNodes = new Set<string>();
  interactions.forEach(interaction => {
    const sourceId = interaction.entity_a?.id?.toString() || '';
    const targetId = interaction.entity_b?.id?.toString() || '';
    if (sourceId) totalUniqueNodes.add(sourceId);
    if (targetId) totalUniqueNodes.add(targetId);
  });

  return {
    nodes: Array.from(nodes.values()),
    edges,
    truncated: totalUniqueNodes.size > MAX_NODES,
    totalNodes: totalUniqueNodes.size,
    displayedNodes: nodes.size
  };
};

const initializeCytoscape = (
  container: HTMLDivElement,
  {
    nodes,
    edges,
    theme,
    entityId,
    onSelectNode,
    onSelectEdge
  }: {
    nodes: GraphNode[];
    edges: GraphEdge[];
    theme: string | undefined;
    entityId?: string;
    onSelectNode: (node: GraphNode | null) => void;
    onSelectEdge: (edge: GraphEdge | null) => void;
  }
) => {
  const cy = cytoscape({
    container,
    elements: {
      nodes: nodes.map((node) => ({
        data: {
          ...node,
          isCenterNode: entityId ? node.id === entityId : false
        }
      })),
      edges: edges.map((edge) => ({
        data: {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          directed: edge.directed,
          consensusSign: edge.consensusSign,
          evidences: edge.evidences
        }
      }))
    },
    style: [
      {
        selector: 'node',
        style: {
          'background-color': (ele: cytoscape.NodeSingular) => {
            const isCenterNode = ele.data('isCenterNode');
            if (isCenterNode) return '#FF0000'; // Red for center node
            return theme === 'dark' ? '#FFFFFF' : '#000000';
          },
          shape: 'ellipse',
          label: 'data(label)',
          'text-opacity': 0,
          color: (ele: cytoscape.NodeSingular) => {
            const isCenterNode = ele.data('isCenterNode');
            if (isCenterNode) return '#FFFFFF';
            return theme === 'dark' ? '#000000' : '#FFFFFF';
          },
          'font-size': 12,
          'font-weight': 500,
          'text-valign': 'center',
          'text-halign': 'center',
          width: (ele: cytoscape.NodeSingular) => {
            const label = ele.data('label').toString();
            const baseWidth = Math.max(label.length * 8, 50);
            const edgeBonus = ele.connectedEdges().length * 2;
            return Math.min(baseWidth + edgeBonus, 120);
          },
          height: (ele: cytoscape.NodeSingular) => {
            const edges = ele.connectedEdges().length;
            // Keep height smaller than width to ensure oval shape
            return Math.min(30 + edges * 1, 40);
          }
        }
      },
      {
        selector: 'edge',
        style: {
          width: 3,
          opacity: 0.7,
          'line-color': (ele: cytoscape.EdgeSingular) => {
            const consensusSign = ele.data('consensusSign');
            if (consensusSign === 'inhibition' || consensusSign === 'negative') {
              return theme === 'dark' ? '#F56565' : '#E53E3E';
            }
            if (consensusSign === 'stimulation' || consensusSign === 'positive') {
              return theme === 'dark' ? '#48BB78' : '#38A169';
            }
            return theme === 'dark' ? '#A0AEC0' : '#718096';
          },
          'target-arrow-shape': (ele: cytoscape.EdgeSingular) => {
            return ele.data('directed') ? 'triangle' : 'none';
          },
          'target-arrow-color': function (ele: cytoscape.EdgeSingular) {
            return ele.style('line-color');
          },
          'curve-style': 'bezier',
          'arrow-scale': 1
        }
      },
      {
        selector: '.highlighted',
        style: {
          'background-color': '#FFA500',
          'text-opacity': 1,
          'z-index': 999999,
          opacity: 1
        }
      },
      {
        selector: '.highlighted-edge',
        style: {
          width: 4,
          opacity: 1,
          'z-index': 999999
        }
      },
      {
        selector: '.faded',
        style: {
          opacity: 0.15,
          'z-index': 0
        }
      },
      {
        selector: '.selected-node',
        style: {
          'background-color': '#FFA500',
          'text-opacity': 1,
          'z-index': 999999,
          opacity: 1
        }
      }
    ],
    layout: entityId ? {
      // When we have a central entity, use cose layout
      name: 'cose',
      animate: false,
      randomize: true,
      componentSpacing: 100,
      nodeRepulsion: function () {
        return 400000;
      },
      idealEdgeLength: function () {
        return 100;
      },
      gravity: 80
    } : {
      // For multi-entity views, use a different layout
      name: 'cose',
      animate: false,
      randomize: false,
      componentSpacing: 150,
      nodeRepulsion: function () {
        return 500000;
      },
      idealEdgeLength: function () {
        return 150;
      },
      gravity: 50,
      // Spread nodes more evenly
      nodeDimensionsIncludeLabels: true
    }
  });

  // Add zoom level handlers
  cy.on('zoom', () => {
    const zoom = cy.zoom();
    cy.style()
      .selector('node')
      .style({
        'text-opacity': zoom > 1.5 ? 1 : 0,
        width: (ele: cytoscape.NodeSingular) => {
          const label = ele.data('label').toString();
          const baseWidth = Math.max(label.length * 8, 50);
          const edgeBonus = ele.connectedEdges().length * 2;
          const width = Math.min(baseWidth + edgeBonus, 120);
          return zoom < 0.5 ? width * 0.7 : width;
        },
        height: (ele: cytoscape.NodeSingular) => {
          const edges = ele.connectedEdges().length;
          const height = Math.min(30 + edges * 1, 40);
          return zoom < 0.5 ? height * 0.7 : height;
        }
      })
      .selector('edge')
      .style({
        opacity: zoom < 0.5 ? 0.3 : 0.7
      })
      .update();
  });

  // Tap handlers
  cy.on('tap', 'node', (evt) => {
    const node = evt.target;
    
    // Clear previous selection
    cy.elements().removeClass('selected-node faded highlighted highlighted-edge');
    
    // Apply selection styling
    node.addClass('selected-node');
    const neighborhood = node.neighborhood().add(node);
    cy.elements().not(neighborhood).addClass('faded');
    node.connectedEdges().addClass('highlighted-edge');
    neighborhood.nodes().style('text-opacity', 1);
    
    onSelectNode(node.data());
  });

  cy.on('tap', 'edge', (evt) => {
    const edge = evt.target;
    onSelectEdge(edge.data());
  });

  cy.on('tap', function (evt) {
    if (evt.target === cy) {
      // Clear all selection states
      cy.elements().removeClass('selected-node faded highlighted highlighted-edge');
      const zoom = cy.zoom();
      cy.nodes().style('text-opacity', zoom > 1.5 ? 1 : 0);
      
      onSelectNode(null);
      onSelectEdge(null);
    }
  });

  // Hover handlers - only apply if no node is selected
  cy.on('mouseover', 'node', function (e) {
    // Don't apply hover effects if a node is selected
    const selectedNodeId = cy.elements('.selected-node').data('id');
    if (selectedNodeId) return;
    
    const node = e.target;
    const neighborhood = node.neighborhood().add(node);
    cy.elements().not(neighborhood).addClass('faded');
    node.addClass('highlighted');
    node.connectedEdges().addClass('highlighted-edge');
    neighborhood.nodes().style('text-opacity', 1);
  });

  cy.on('mouseout', 'node', function () {
    // Don't remove hover effects if a node is selected
    const selectedNodeId = cy.elements('.selected-node').data('id');
    if (selectedNodeId) return;
    
    cy.elements().removeClass('faded');
    cy.elements().removeClass('highlighted');
    cy.elements().removeClass('highlighted-edge');

    const zoom = cy.zoom();
    cy.nodes().style('text-opacity', zoom > 1.5 ? 1 : 0);
  });

  return cy;
};

export const GraphView: React.FC<GraphViewProps> = React.memo(
  ({ interactions, entityId, onSelectInteraction }) => {
    const { theme, systemTheme } = useTheme();
    const effectiveTheme = theme === 'system' ? systemTheme : theme;
    const cyRef = useRef<cytoscape.Core | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
    const [nodeDocument, setNodeDocument] = useState<Record<string, unknown> & { id: string } | null>(null);
    const [isLoadingDocument, setIsLoadingDocument] = useState(false);

    // Transform data
    const graphData = React.useMemo(() => 
      transformToGraphData(interactions, entityId), 
      [interactions, entityId]
    );

    // Fetch entity document when node is selected
    useEffect(() => {
      const fetchDocument = async () => {
        if (!selectedNode) {
          setNodeDocument(null);
          return;
        }

        setIsLoadingDocument(true);
        try {
          // Try to fetch by entity ID first, then by canonical identifier
          let doc = await fetchEntityDocument(selectedNode.id);
          if (!doc && selectedNode.canonicalIdentifier) {
            doc = await fetchEntityDocument(selectedNode.canonicalIdentifier);
          }
          setNodeDocument(doc);
        } catch (error) {
          console.error('Error fetching entity document:', error);
          setNodeDocument(null);
        } finally {
          setIsLoadingDocument(false);
        }
      };

      fetchDocument();
    }, [selectedNode]);

    // Initialization effect
    useEffect(() => {
      if (!containerRef.current) return;

      const cy = initializeCytoscape(containerRef.current, {
        nodes: graphData.nodes,
        edges: graphData.edges,
        theme: effectiveTheme,
        entityId,
        onSelectNode: setSelectedNode,
        onSelectEdge: (edge) => {
          // Find the corresponding interaction for the edge
          const interaction = interactions.find(i => i.id?.toString() === edge?.id);
          if (interaction && onSelectInteraction) {
            onSelectInteraction(interaction);
          }
        }
      });

      cyRef.current = cy;

      return () => {
        cy.destroy();
      };
    }, [graphData, effectiveTheme, entityId, interactions, onSelectInteraction]);

    const handleFitView = useCallback(() => {
      if (!cyRef.current) return;
      
      // Store current node dimensions before fit
      const cy = cyRef.current;
      const nodeStyles = new Map();
      
      cy.nodes().forEach((node) => {
        nodeStyles.set(node.id(), {
          width: node.style('width'),
          height: node.style('height')
        });
      });
      
      // Perform fit
      cy.fit();
      
      // Restore node dimensions after fit
      cy.nodes().forEach((node) => {
        const styles = nodeStyles.get(node.id());
        if (styles) {
          node.style(styles);
        }
      });
      
      // Update text opacity based on new zoom level
      const zoom = cy.zoom();
      cy.nodes().style('text-opacity', zoom > 1.5 ? 1 : 0);
    }, []);

    return (
      <div className="w-full h-[600px] relative">
        <div ref={containerRef} className="w-full h-full" />

        {/* Controls */}
        <div className="absolute top-0 right-0 m-4 p-4 rounded-md bg-white dark:bg-gray-800 text-black dark:text-white shadow-md z-50">
          <p>Nodes: {graphData.nodes.length}</p>
          <p>Edges: {graphData.edges.length}</p>
          {graphData.truncated && (
            <p className="text-xs text-amber-600 dark:text-amber-400 mt-2">
              Limited to 200 nodes<br/>
              ({graphData.totalNodes} total)
            </p>
          )}
        </div>

        <div className="absolute top-4 left-4 z-50 flex gap-2">
          <Button onClick={handleFitView} variant="outline">
            Fit View
          </Button>
          
          <Dialog>
            <DialogTrigger asChild>
              <Button
                variant="outline"
                onClick={(e) => {
                  if (!selectedNode) {
                    e.preventDefault();
                    toast.info("Please select a node first");
                  }
                }}
              >
                Node Info
                {selectedNode && ` (${selectedNode.label})`}
              </Button>
            </DialogTrigger>
            {selectedNode && (
              <DialogContent className="p-6 max-w-2xl">
                <DialogTitle className="sr-only">
                  Entity Information for {selectedNode.label}
                </DialogTitle>
                <div className="space-y-4">
                  {isLoadingDocument ? (
                    <div className="flex items-center justify-center p-8">
                      <div className="text-muted-foreground">Loading entity information...</div>
                    </div>
                  ) : nodeDocument ? (
                    <ResultCard result={nodeDocument} />
                  ) : (
                    <div className="space-y-4">
                      <div className="text-center text-muted-foreground p-8">
                        Could not load detailed information for this entity.
                      </div>
                      <div className="border rounded-lg p-4 bg-muted/50">
                        <h3 className="font-semibold mb-2">Basic Information</h3>
                        <div className="space-y-2 text-sm">
                          <div>
                            <span className="font-medium">ID:</span> {selectedNode.id}
                          </div>
                          <div>
                            <span className="font-medium">Label:</span> {selectedNode.label}
                          </div>
                          <div>
                            <span className="font-medium">Type:</span> {selectedNode.type}
                          </div>
                          {selectedNode.canonicalIdentifier && (
                            <div>
                              <span className="font-medium">Canonical ID:</span> {selectedNode.canonicalIdentifier}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              </DialogContent>
            )}
          </Dialog>
        </div>
      </div>
    );
  }
);

GraphView.displayName = "GraphView";

// Export as dynamic component to avoid SSR issues
const GraphViewNoSSR = dynamic(() => Promise.resolve(GraphView), {
  ssr: false
});

export default GraphViewNoSSR;