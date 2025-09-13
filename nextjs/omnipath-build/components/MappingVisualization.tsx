'use client';

import { useMemo, useState, useCallback, useEffect } from 'react';
import { Background, Edge, ReactFlow, Node, MarkerType, Controls, useNodesState, useEdgesState } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import DatabaseSchemaDemo from "./react-flow-vis";

const nodeTypes = {
  databaseSchema: DatabaseSchemaDemo,
};

interface FieldMapping {
  source: string;
  target: string;
  transform?: string;
  description?: string;
}

interface MappingData {
  resource: string;
  module: string;
  function: string;
  targetTable: string;
  fieldMappings: FieldMapping[];
  description: string;
}

interface MappingVisualizationProps {
  mappings: MappingData[];
  bronzeTables: Array<{ resource: string; table: string }>;
  silverTables: string[];
  silverTableDefinitions?: Record<string, Record<string, string>>;
}

export default function MappingVisualization({ 
  mappings, 
  bronzeTables, 
  silverTables,
  silverTableDefinitions 
}: MappingVisualizationProps) {
  
  const { nodes: initialNodes, edges: initialEdges } = useMemo(() => {
    const nodes: Node[] = [];
    const edges: Edge[] = [];
    
    let yPosition = 0;
    const nodeSpacing = 350;
    const bronzeX = 0;
    const silverX = 600;

    // Group mappings by resource and target table to avoid duplicates
    const resourceGroups = new Map<string, MappingData[]>();
    const targetGroups = new Map<string, Set<string>>();
    
    mappings.forEach(mapping => {
      const resourceKey = `${mapping.resource}-${mapping.module}-${mapping.function}`;
      if (!resourceGroups.has(resourceKey)) {
        resourceGroups.set(resourceKey, []);
      }
      resourceGroups.get(resourceKey)!.push(mapping);
      
      // Track unique fields for target tables
      if (!targetGroups.has(mapping.targetTable)) {
        targetGroups.set(mapping.targetTable, new Set());
      }
      mapping.fieldMappings.forEach(fm => {
        targetGroups.get(mapping.targetTable)!.add(fm.target);
      });
    });

    // Create nodes for each unique resource group
    let nodeIndex = 0;
    resourceGroups.forEach((groupMappings) => {
      const firstMapping = groupMappings[0];
      const bronzeNodeId = `bronze-${nodeIndex}`;
      const silverNodeId = `silver-${firstMapping.targetTable}-${nodeIndex}`;
      
      // Collect all unique source fields
      const sourceFieldsSet = new Set<string>();
      groupMappings.forEach(mapping => {
        mapping.fieldMappings.forEach(fm => sourceFieldsSet.add(fm.source));
      });
      
      const bronzeFields = Array.from(sourceFieldsSet).map((field, idx) => ({
        title: field,
        type: 'varchar',
        key: `${bronzeNodeId}-field-${idx}` // Unique key for each field
      }));
      
      // Create bronze node
      nodes.push({
        id: bronzeNodeId,
        position: { x: bronzeX, y: yPosition },
        type: 'databaseSchema',
        data: {
          label: `${firstMapping.resource} - ${firstMapping.function}`,
          schema: bronzeFields,
        },
        style: {
          backgroundColor: '#fef3c7',
          borderColor: '#f59e0b',
        }
      });
      
      // Get all silver table fields from table definitions if available
      let silverFields;
      if (silverTableDefinitions && silverTableDefinitions[firstMapping.targetTable]) {
        // Use all columns from the silver table definition
        const tableDefinition = silverTableDefinitions[firstMapping.targetTable];
        silverFields = Object.keys(tableDefinition).map((field, idx) => {
          // Extract just the type from the definition (e.g., "VARCHAR" from "VARCHAR DEFAULT FALSE")
          const typeDefinition = tableDefinition[field];
          const type = typeDefinition.split(' ')[0].toLowerCase();
          
          return {
            title: field,
            type: type,
            key: `${silverNodeId}-field-${idx}`
          };
        });
      } else {
        // Fallback to only mapped fields if no table definition available
        const targetFieldsSet = new Set<string>();
        groupMappings.forEach(mapping => {
          mapping.fieldMappings.forEach(fm => targetFieldsSet.add(fm.target));
        });
        
        silverFields = Array.from(targetFieldsSet).map((field, idx) => ({
          title: field,
          type: 'varchar',
          key: `${silverNodeId}-field-${idx}`
        }));
      }
      
      // Create silver node
      nodes.push({
        id: silverNodeId,
        position: { x: silverX, y: yPosition },
        type: 'databaseSchema',
        data: {
          label: `${firstMapping.targetTable} (Silver)`,
          schema: silverFields,
        },
        style: {
          backgroundColor: '#f3f4f6',
          borderColor: '#6b7280',
        }
      });
      
      // Create edges for field mappings
      groupMappings.forEach((mapping) => {
        mapping.fieldMappings.forEach((fieldMapping, fieldIndex) => {
          const edge: Edge = {
            id: `edge-${nodeIndex}-${fieldIndex}-${fieldMapping.source}-${fieldMapping.target}`,
            source: bronzeNodeId,
            target: silverNodeId,
            sourceHandle: fieldMapping.source,
            targetHandle: fieldMapping.target,
            type: 'bezier',
            markerEnd: {
              type: MarkerType.ArrowClosed,
              color: '#3b82f6',
            },
          };
          
          // Add label if transformation exists
          if (fieldMapping.transform) {
            edge.label = fieldMapping.transform;
            edge.labelStyle = {
              fontSize: 11,
              fontWeight: 500,
              fill: '#6b7280',
              backgroundColor: 'rgba(255, 255, 255, 0.9)',
              padding: '2px 6px',
              borderRadius: '4px',
              border: '1px solid #e5e7eb',
            };
            edge.labelBgStyle = {
              fill: 'transparent',
            };
          }
          
          edges.push(edge);
        });
      });
      
      yPosition += nodeSpacing;
      nodeIndex++;
    });
    
    // If no mappings, show placeholder nodes
    if (mappings.length === 0) {
      // Add bronze placeholder
      if (bronzeTables.length > 0) {
        bronzeTables.slice(0, 3).forEach((table, index) => {
          nodes.push({
            id: `bronze-placeholder-${index}`,
            position: { x: bronzeX, y: index * nodeSpacing },
            type: 'databaseSchema',
            data: {
              label: `${table.resource}/${table.table} (Bronze)`,
              schema: [
                { title: 'No mappings defined', type: 'info' }
              ],
            },
            style: {
              backgroundColor: '#fef3c7',
              borderColor: '#f59e0b',
              opacity: 0.6,
            }
          });
        });
      }
      
      // Add silver placeholder
      if (silverTables.length > 0) {
        silverTables.slice(0, 3).forEach((table, index) => {
          nodes.push({
            id: `silver-placeholder-${index}`,
            position: { x: silverX, y: index * nodeSpacing },
            type: 'databaseSchema',
            data: {
              label: `${table} (Silver)`,
              schema: [
                { title: 'No mappings defined', type: 'info' }
              ],
            },
            style: {
              backgroundColor: '#f3f4f6',
              borderColor: '#6b7280',
              opacity: 0.6,
            }
          });
        });
      }
    }
    
    return { nodes, edges };
  }, [mappings, bronzeTables, silverTables, silverTableDefinitions]);

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  // Update nodes and edges when props change
  useEffect(() => {
    setNodes(initialNodes);
    setEdges(initialEdges);
  }, [initialNodes, initialEdges, setNodes, setEdges]);

  return (
    <div className="h-full w-full rounded-lg overflow-hidden border border-gray-200 dark:border-gray-700">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView>
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  );
}
