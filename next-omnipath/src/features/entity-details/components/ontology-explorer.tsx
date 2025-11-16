"use client"

import { useState, useEffect } from "react"
import { ChevronRight, ChevronDown, Database, Star, FileText, Folder } from "lucide-react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { fetchCvTermsByIds } from "@/lib/meilisearch/search"
import Link from "next/link"

interface CvTermData {
  id: string;
  name: string;
  namespace?: string;
  definition?: string;
  synonyms?: string[];
  associatedEntityIds?: string[];
  direct_parent_ids?: string[];
  category_id?: string;
}

interface OntologyNode {
  id: string
  name: string
  namespace?: string
  definition?: string
  synonyms?: string[]
  is_annotated?: boolean
  is_namespace?: boolean
  children?: OntologyNode[]
  associated_entity_ids?: string[]
  direct_parent_ids?: string[];
  category_id?: string;
}

interface OntologyExplorerProps {
  cvTermIds?: string[]
  entityId?: string
}

export function OntologyExplorer({ 
  cvTermIds = [],
  entityId
}: OntologyExplorerProps) {
  const [selectedTerm, setSelectedTerm] = useState<OntologyNode | null>(null)
  const [expandedNodes, setExpandedNodes] = useState(new Set<string>())
  const [minimalTree, setMinimalTree] = useState<OntologyNode[]>([])
  const [loading, setLoading] = useState(true)
  
  // Build minimal tree from CV term IDs
  useEffect(() => {
    async function buildMinimalTree() {
      console.log('OntologyExplorer received cvTermIds:', cvTermIds);
      if (cvTermIds.length === 0) {
        setLoading(false)
        return
      }
      
      try {
        // Fetch all CV terms
        const { documents } = await fetchCvTermsByIds(cvTermIds)
        console.log(documents)
        console.log((cvTermIds.length, documents.length))

        console.log(documents)
        const terms = documents as unknown as CvTermData[]
        
        // Only fetch category terms, not direct parents
        const categoryIds = new Set<string>()
        terms.forEach(term => {
          if (term.category_id) categoryIds.add(term.category_id)
        })
        
        // Remove IDs we already have
        cvTermIds.forEach(id => categoryIds.delete(id))
        
        // Fetch category terms if needed
        let allTerms = terms
        if (categoryIds.size > 0) {
          const { documents: categoryDocs } = await fetchCvTermsByIds(Array.from(categoryIds))
          allTerms = [...terms, ...(categoryDocs as unknown as CvTermData[])]
        }
        
        // Create a map for quick lookup
        const termMap = new Map<string, CvTermData>()
        allTerms.forEach(term => termMap.set(term.id, term))
        
        // Build the tree structure (simplified: Namespace -> Category -> Term)
        const namespaceGroups = new Map<string, OntologyNode>()
        
        // Process terms that are associated with the entity
        cvTermIds.forEach(termId => {
          const term = termMap.get(termId)
          if (!term) return
          
          const namespace = term.namespace || 'Unknown'
          
          // Get or create namespace node
          if (!namespaceGroups.has(namespace)) {
            namespaceGroups.set(namespace, {
              id: `namespace_${namespace}`,
              name: namespace,
              is_namespace: true,
              children: []
            })
          }
          
          const namespaceNode = namespaceGroups.get(namespace)!
          
          // Create term node
          const termNode: OntologyNode = {
            id: term.id,
            name: term.name,
            namespace: term.namespace,
            definition: term.definition,
            synonyms: term.synonyms,
            is_annotated: true,
            associated_entity_ids: term.associatedEntityIds,
            direct_parent_ids: term.direct_parent_ids,
            category_id: term.category_id
          }
          
          // If there's a category, create that level
          if (term.category_id && termMap.has(term.category_id)) {
            const categoryTerm = termMap.get(term.category_id)!
            let categoryNode = namespaceNode.children?.find(c => c.id === categoryTerm.id)
            
            if (!categoryNode) {
              categoryNode = {
                id: categoryTerm.id,
                name: categoryTerm.name,
                namespace: categoryTerm.namespace,
                children: []
              }
              namespaceNode.children!.push(categoryNode)
            }
            
            // Add term under category
            if (!categoryNode.children!.find(c => c.id === termNode.id)) {
              categoryNode.children!.push(termNode)
            }
          } else {
            // Add directly under namespace if no category
            if (!namespaceNode.children!.find(c => c.id === termNode.id)) {
              namespaceNode.children!.push(termNode)
            }
          }
        })
        
        // Convert to array and sort
        const tree = Array.from(namespaceGroups.values()).sort((a, b) => 
          a.name.localeCompare(b.name)
        )
        
        setMinimalTree(tree)
        
        // Auto-expand namespaces and select first term
        const newExpanded = new Set<string>()
        tree.forEach(ns => {
          newExpanded.add(ns.id)
          // Expand categories too
          ns.children?.forEach(child => {
            if (child.children && child.children.length > 0) {
              newExpanded.add(child.id)
            }
          })
        })
        setExpandedNodes(newExpanded)
        
        // Select first annotated term
        const firstAnnotatedTerm = tree
          .flatMap(ns => ns.children || [])
          .flatMap(node => node.children ? [node, ...node.children] : [node])
          .find(term => term.is_annotated)
        
        if (firstAnnotatedTerm) {
          setSelectedTerm(firstAnnotatedTerm)
        }
        
      } catch (error) {
        console.error('Error building minimal tree:', error)
      } finally {
        setLoading(false)
      }
    }
    
    buildMinimalTree()
  }, [cvTermIds])

  const toggleNode = (nodeId: string) => {
    const newExpanded = new Set(expandedNodes)
    if (newExpanded.has(nodeId)) {
      newExpanded.delete(nodeId)
    } else {
      newExpanded.add(nodeId)
    }
    setExpandedNodes(newExpanded)
  }

  const handleTermSelect = (term: OntologyNode) => {
    setSelectedTerm(term)
  }

  const renderTree = (nodes: OntologyNode[], level = 0) => {
    return nodes.map((node) => {
      const hasChildren = node.children && node.children.length > 0
      const isExpanded = expandedNodes.has(node.id)
      const isSelected = selectedTerm?.id === node.id
      
      const colors = node.is_namespace ? {
        text: "text-muted-foreground",
        border: "border-muted",
        hover: "hover:bg-muted"
      } : {
        text: "text-foreground",
        border: "border-border",
        hover: "hover:bg-accent"
      }

      return (
        <div key={node.id}>
          <div
            className={`
              flex items-center gap-1 px-2 py-1 cursor-pointer rounded-md transition-colors
              ${colors.hover}
              ${isSelected ? 'bg-accent' : ''}
              ${node.is_annotated ? 'font-medium' : ''}
            `}
            style={{ paddingLeft: `${level * 12 + 8}px` }}
            onClick={() => {
              if (hasChildren) toggleNode(node.id)
              if (!node.is_namespace) handleTermSelect(node)
            }}
          >
            {hasChildren && (
              <div className="p-0.5">
                {isExpanded ? (
                  <ChevronDown className="h-3 w-3" />
                ) : (
                  <ChevronRight className="h-3 w-3" />
                )}
              </div>
            )}
            {!hasChildren && (
              <div className="w-4" />
            )}
            
            <div className="flex items-center gap-1.5 flex-1">
              {node.is_namespace ? (
                <Database className="h-3.5 w-3.5 text-muted-foreground" />
              ) : node.is_annotated ? (
                <Star className="h-3.5 w-3.5 text-yellow-500" />
              ) : hasChildren ? (
                <Folder className="h-3.5 w-3.5 text-muted-foreground" />
              ) : (
                <FileText className="h-3.5 w-3.5 text-muted-foreground" />
              )}
              
              <span className={`text-sm ${colors.text}`}>
                {node.name}
              </span>
            </div>
          </div>
          
          {hasChildren && isExpanded && (
            <div>
              {renderTree(node.children!, level + 1)}
            </div>
          )}
        </div>
      )
    })
  }

  if (loading) {
    return (
      <div className="flex gap-4">
        <Card className="w-96 h-[600px] animate-pulse">
          <CardContent className="p-4">
            <div className="h-full bg-muted rounded" />
          </CardContent>
        </Card>
        <Card className="flex-1 animate-pulse">
          <CardContent className="p-6">
            <div className="space-y-4">
              <div className="h-8 bg-muted rounded w-1/3" />
              <div className="h-4 bg-muted rounded w-full" />
              <div className="h-4 bg-muted rounded w-3/4" />
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (minimalTree.length === 0) {
    return (
      <Card>
        <CardContent className="p-6">
          <p className="text-muted-foreground">No ontology terms found for this entity.</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="flex gap-4 overflow-hidden">
      {/* Tree Navigation */}
      <Card className="w-80 min-w-0 flex-shrink-0 h-[600px] flex flex-col">
        <div className="p-4 border-b flex-shrink-0">
          <h3 className="font-semibold text-sm">Ontology Terms</h3>
          <p className="text-xs text-muted-foreground mt-1">
            Terms annotated to this entity
          </p>
        </div>
        <ScrollArea className="flex-1 min-h-0">
          <div className="p-2">
            {renderTree(minimalTree)}
          </div>
        </ScrollArea>
      </Card>

      {/* Term Details */}
      {selectedTerm && !selectedTerm.is_namespace && (
        <Card className="flex-1 min-w-0 overflow-hidden">
          <CardContent className="p-6">
            <div className="space-y-4">              
              <div>
                <h3 className="text-xl font-semibold flex items-center gap-2">
                  {selectedTerm.name}
                  {selectedTerm.namespace && (
                    <Badge variant="outline" className="text-xs">
                      {selectedTerm.namespace}
                    </Badge>
                  )}
                </h3>
                <p className="text-sm text-muted-foreground mt-1">
                  ID: {selectedTerm.id}
                </p>
              </div>

              {selectedTerm.definition && (
                <div>
                  <h4 className="font-medium text-sm mb-1">Definition</h4>
                  <p className="text-sm text-muted-foreground">
                    {selectedTerm.definition}
                  </p>
                </div>
              )}

              {selectedTerm.synonyms && selectedTerm.synonyms.length > 0 && (
                <div>
                  <h4 className="font-medium text-sm mb-1">Synonyms</h4>
                  <div className="flex flex-wrap gap-1">
                    {selectedTerm.synonyms.map((synonym, idx) => (
                      <Badge key={idx} variant="secondary" className="text-xs">
                        {synonym}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              <div className="pt-4">
                <Link 
                  href={`/cv_term/${selectedTerm.id}`}
                  className="text-sm text-primary hover:underline inline-flex items-center gap-1"
                >
                  View full term details
                  <ChevronRight className="h-3 w-3" />
                </Link>
              </div>

              {/* Associated Entities */}
              {selectedTerm.associated_entity_ids && selectedTerm.associated_entity_ids.length > 0 && entityId && (
                <div className="pt-4 border-t">
                  <h4 className="font-medium text-sm mb-3">
                    Other entities with this term ({selectedTerm.associated_entity_ids.length})
                  </h4>
                  <div className="space-y-2 max-h-60 overflow-y-auto">
                    {selectedTerm.associated_entity_ids
                      .filter(id => id !== entityId)
                      .slice(0, 10)
                      .map((entityId) => (
                        <div key={entityId} className="text-sm">
                          <Link 
                            href={`/entity/${entityId}`}
                            className="text-primary hover:underline"
                          >
                            Entity {entityId}
                          </Link>
                        </div>
                      ))}
                    {selectedTerm.associated_entity_ids.length > 10 && (
                      <p className="text-xs text-muted-foreground">
                        ... and {selectedTerm.associated_entity_ids.length - 10} more
                      </p>
                    )}
                  </div>
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}