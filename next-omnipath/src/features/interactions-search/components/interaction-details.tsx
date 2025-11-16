import { Badge } from "@/components/ui/badge"
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion"
import { FileText, Search, ArrowRight, Minus, FlaskConical, Microscope, Dna, BarChart3, ExternalLink } from "lucide-react"
import { EntityBadge } from "@/components/entity-badge"
import { CvTermBadge } from "@/features/cv-terms/components/cv-term-badge"
import { cn } from "@/lib/utils"
import { useMemo } from "react"

// Type for interaction data - matches the format used by GraphView
interface InteractionData {
  id?: string | number
  entity_a?: {
    id?: string
    canonical_identifier?: string
    display_name?: string
  }
  entity_b?: {
    id?: string
    canonical_identifier?: string
    display_name?: string
  }
  has_directed_evidence?: boolean
  consensus_sign?: string | null
  evidences?: Array<{
    id: number
    sign?: string | null
    is_directed?: boolean
    direction?: string
    causal_statement?: { id: string; name: string }
    interaction_type?: { id: string; name: string }
    causal_mechanism?: { id: string; name: string }
    detection_methods?: Array<{ id: string; name: string }>
    evidence_sentence?: string
    reference?: { pubmed_id?: number }
    data_source?: { name: string }
    participants?: Array<{
      entity?: { canonical_identifier?: string }
      stoichiometry?: number
      biological_roles?: Array<{ id: string; name: string }>
      experimental_roles?: Array<{ id: string; name: string }>
      interactor_types?: Array<{ id: string; name: string }>
    }>
  }>
}

interface InteractionDetailsProps {
  selectedInteraction: InteractionData | null
}

// Detection method icons mapping
const DETECTION_METHOD_ICONS: Record<string, React.ReactNode> = {
  'western blot': <FlaskConical className="h-4 w-4" />,
  'mass spectrometry': <BarChart3 className="h-4 w-4" />,
  'yeast two-hybrid': <Dna className="h-4 w-4" />,
  'immunoprecipitation': <FlaskConical className="h-4 w-4" />,
  'fluorescence microscopy': <Microscope className="h-4 w-4" />,
  'pull down': <FlaskConical className="h-4 w-4" />,
  'default': <FlaskConical className="h-4 w-4" />
}

const getDetectionMethodIcon = (methodName: string) => {
  const lowerMethod = methodName.toLowerCase()
  return DETECTION_METHOD_ICONS[lowerMethod] || DETECTION_METHOD_ICONS['default']
}

const getSignColor = (sign: string | null | undefined, negative?: boolean) => {
  if ((sign === '+' || sign === 'positive') && !negative) return 'text-green-600 bg-green-50 border-green-200'
  if (sign === '-' || sign === 'negative' || negative) return 'text-red-600 bg-red-50 border-red-200'
  if (sign === '?' || sign === 'unknown') return 'text-yellow-600 bg-yellow-50 border-yellow-200'
  return 'text-gray-600 bg-gray-50 border-gray-200'
}

const getSignLabel = (sign: string | null | undefined, negative?: boolean) => {
  if ((sign === '+' || sign === 'positive') && !negative) return 'Stimulation'
  if (sign === '-' || sign === 'negative' || negative) return 'Inhibition'
  if (sign === '?' || sign === 'unknown') return 'Unknown'
  return 'Unspecified'
}

export function InteractionDetails({ selectedInteraction }: InteractionDetailsProps) {
  const getInteractionColor = () => {
    if (!selectedInteraction) return "text-gray-500";
    
    // First check consensus sign
    if (selectedInteraction.consensus_sign === 'positive') return "text-green-500";
    if (selectedInteraction.consensus_sign === 'negative') return "text-red-500";
    
    // If no consensus sign, check evidence
    if (!selectedInteraction.evidences || selectedInteraction.evidences.length === 0) {
      return "text-gray-500"; // Default if no evidences
    }
    
    // Check for any positive or negative evidence
    const hasPositive = selectedInteraction.evidences.some(e => 
      e.sign === 'positive' || e.interaction_type?.name?.toLowerCase().includes('stimulation')
    );
    const hasNegative = selectedInteraction.evidences.some(e => 
      e.sign === 'negative' || e.interaction_type?.name?.toLowerCase().includes('inhibition')
    );
    
    if (hasPositive && !hasNegative) return "text-green-500";
    if (hasNegative && !hasPositive) return "text-red-500";
    if (hasPositive && hasNegative) return "text-orange-500"; // Mixed
    
    return "text-gray-500"; // Unknown/unspecified
  }

  const evidenceStats = useMemo(() => {
    if (!selectedInteraction?.evidences) return null;

    const stats = {
      total: selectedInteraction.evidences.length,
      withReferences: selectedInteraction.evidences.filter(e => e.reference?.pubmed_id).length,
      directed: selectedInteraction.evidences.filter(e => e.causal_statement).length,
      sources: [...new Set(selectedInteraction.evidences.map(e => e.data_source?.name).filter(Boolean))],
      detectionMethods: [...new Set(selectedInteraction.evidences.flatMap(e => 
        e.detection_methods?.map(dm => dm.name) || []
      ))],
      withSentences: selectedInteraction.evidences.filter(e => e.evidence_sentence).length
    };

    return stats;
  }, [selectedInteraction]);

  const referencesData = useMemo(() => {
    if (!selectedInteraction?.evidences) return null;

    const acc: Record<string, string[]> = {};
    selectedInteraction.evidences.forEach(evidence => {
      const pubmedId = evidence.reference?.pubmed_id?.toString();
      const sourceName = evidence.data_source?.name;

      if (pubmedId && sourceName) {
        if (!acc[pubmedId]) {
          acc[pubmedId] = [];
        }
        if (!acc[pubmedId].includes(sourceName)) {
          acc[pubmedId].push(sourceName);
        }
      }
    });
    return Object.entries(acc);
  }, [selectedInteraction]);

  if (!selectedInteraction) {
    return (
      <div className="p-4">
        <div className="rounded-lg border bg-card p-8">
          <div className="flex flex-col items-center justify-center text-center">
            <Search className="h-12 w-12 text-muted-foreground mb-4" />
            <p className="text-lg font-medium text-muted-foreground mb-2">No interaction selected</p>
            <p className="text-sm text-muted-foreground">Select an interaction to view detailed evidence</p>
          </div>
        </div>
      </div>
    )
  }

  // Entities are already in the correct order from the parent component
  // No need to swap here as it's handled consistently at the data level
  const sourceEntity = selectedInteraction?.entity_a;
  const targetEntity = selectedInteraction?.entity_b;

  return (
    <div className="p-4 pb-8 space-y-6">
      {/* ===== INTERACTION OVERVIEW ===== */}
      <div className="rounded-lg border bg-card p-6">
        {/* Entity Visualization */}
        <div className="flex items-center justify-center gap-6 py-6">
          <EntityBadge 
            displayName={sourceEntity?.display_name || ''} 
            canonicalIdentifier={sourceEntity?.canonical_identifier || ''} 
          />
          
          <div className="flex flex-col items-center gap-2">
            <div className={cn("flex items-center", getInteractionColor())}>
              {selectedInteraction.has_directed_evidence ? (
                <ArrowRight className="h-8 w-8" />
              ) : (
                <Minus className="h-8 w-8" />
              )}
            </div>
            {selectedInteraction.consensus_sign && (
              <Badge className={cn("text-xs px-2 py-1 border", getSignColor(selectedInteraction.consensus_sign))}>
                {getSignLabel(selectedInteraction.consensus_sign)}
              </Badge>
            )}
          </div>

          <EntityBadge 
            displayName={targetEntity?.display_name || ''} 
            canonicalIdentifier={targetEntity?.canonical_identifier || ''} 
          />
        </div>

        {/* Evidence Summary Stats */}
        {evidenceStats && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-4 border-t">
            <div className="text-center">
              <div className="text-2xl font-bold text-primary">{evidenceStats.total}</div>
              <div className="text-xs text-muted-foreground">Evidence{evidenceStats.total !== 1 ? 's' : ''}</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-green-600">{referencesData?.length || 0}</div>
              <div className="text-xs text-muted-foreground">Reference{(referencesData?.length || 0) !== 1 ? 's' : ''}</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-blue-600">{evidenceStats.sources.length}</div>
              <div className="text-xs text-muted-foreground">Source{evidenceStats.sources.length !== 1 ? 's' : ''}</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-purple-600">{evidenceStats.detectionMethods.length}</div>
              <div className="text-xs text-muted-foreground">Method{evidenceStats.detectionMethods.length !== 1 ? 's' : ''}</div>
            </div>
          </div>
        )}
      </div>

      {/* ===== PROGRESSIVE DISCLOSURE ACCORDIONS ===== */}
      <Accordion type="multiple" defaultValue={["evidence"]} className="space-y-4">
        
        {/* References Summary */}
        {referencesData && referencesData.length > 0 && (
          <AccordionItem value="references" className="border rounded-lg">
            <AccordionTrigger className="px-4 py-3 hover:no-underline">
              <div className="flex items-center gap-2">
                <ExternalLink className="h-5 w-5" />
                <span className="font-medium">References</span>
                <Badge variant="secondary" className="ml-2">
                  {referencesData.length}
                </Badge>
              </div>
            </AccordionTrigger>
            <AccordionContent className="px-4 pb-4">
              <div className="space-y-4">
                {referencesData.map(([pubmedId, sourcesArray], index) => (
                  <div key={pubmedId} className="flex items-start gap-3 p-3 border rounded-lg bg-muted/30">
                    <span className="text-muted-foreground min-w-[2rem] text-sm font-mono">
                      [{index + 1}]
                    </span>
                    <div className="flex-1 space-y-2">
                      <a
                        href={`https://pubmed.ncbi.nlm.nih.gov/${pubmedId}/`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-primary hover:underline font-medium"
                      >
                        <ExternalLink className="h-4 w-4" />
                        PMID: {pubmedId}
                      </a>
                      <div className="flex flex-wrap gap-2">
                        {(sourcesArray as string[]).map((sourceName: string) => (
                          <Badge key={sourceName} variant="secondary" className="text-xs">
                            {sourceName}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </AccordionContent>
          </AccordionItem>
        )}
        
        {/* Evidence Details */}
        <AccordionItem value="evidence" className="border rounded-lg">
          <AccordionTrigger className="px-4 py-3 hover:no-underline">
            <div className="flex items-center gap-2">
              <FileText className="h-5 w-5" />
              <span className="font-medium">Evidence Details</span>
              <Badge variant="secondary" className="ml-2">
                {selectedInteraction.evidences?.length || 0}
              </Badge>
            </div>
          </AccordionTrigger>
          <AccordionContent className="px-4 pb-4">
            <div className="space-y-6">
              {selectedInteraction.evidences?.map((evidence, index) => (
                <div key={evidence.id} className="border rounded-lg p-4 bg-muted/30">
                  {/* Evidence Header */}
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <Badge variant="outline" className="text-xs">
                        Evidence {index + 1} (ID: {evidence.id})
                      </Badge>
                      {evidence.data_source?.name && (
                        <Badge variant="secondary" className="text-xs">
                          {evidence.data_source.name}
                        </Badge>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      {evidence.is_directed && (
                        <Badge className="text-xs bg-blue-100 text-blue-700 border-blue-200">
                          Directed
                        </Badge>
                      )}
                      {evidence.sign && (
                        <Badge className={cn("text-xs", getSignColor(evidence.sign))}>
                          {getSignLabel(evidence.sign)}
                        </Badge>
                      )}
                    </div>
                  </div>

                  {/* Evidence Sentence (Priority 1) */}
                  {evidence.evidence_sentence && (
                    <div className="mb-4 p-3 bg-blue-50 border-l-4 border-blue-400 rounded-r">
                      <div className="text-sm font-medium text-blue-900 mb-1">Evidence Sentence</div>
                      <p className="text-sm text-blue-800 italic">&quot;{evidence.evidence_sentence}&quot;</p>
                    </div>
                  )}

                  {/* Interaction Properties */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                    {evidence.interaction_type?.name && (
                      <div>
                        <div className="text-xs font-medium text-muted-foreground mb-1">Type</div>
                        <CvTermBadge 
                          cvTermId={evidence.interaction_type.id} 
                          cvTermName={evidence.interaction_type.name} 
                          variant="outline" 
                        />
                      </div>
                    )}  
                    {evidence.causal_mechanism?.name && (
                      <div>
                        <div className="text-xs font-medium text-muted-foreground mb-1">Mechanism</div>
                        <CvTermBadge 
                          cvTermId={evidence.causal_mechanism.id} 
                          cvTermName={evidence.causal_mechanism.name} 
                          variant="outline" 
                        />
                      </div>
                    )}
                    {evidence.causal_statement?.name && (
                      <div>
                        <div className="text-xs font-medium text-muted-foreground mb-1">Effect</div>
                        <CvTermBadge 
                          cvTermId={evidence.causal_statement.id} 
                          cvTermName={evidence.causal_statement.name} 
                          variant="outline" 
                        />
                      </div>
                    )}
                  </div>

                  {/* Detection Methods */}
                  {evidence.detection_methods && evidence.detection_methods.length > 0 && (
                    <div className="mb-4">
                      <div className="text-xs font-medium text-muted-foreground mb-2">Detection Methods</div>
                      <div className="flex flex-wrap gap-2">
                        {evidence.detection_methods.map((method, methodIndex) => (
                          <div key={methodIndex} className="flex items-center gap-1">
                            {getDetectionMethodIcon(method.name)}
                            <CvTermBadge 
                              cvTermId={method.id}
                              cvTermName={method.name}
                              variant="secondary" 
                              className="text-xs"
                            />
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Participants (Minimal) */}
                  {evidence.participants && evidence.participants.length > 0 && (() => {
                    // Check if any participant has meaningful roles/types
                    const participantsWithRoles = evidence.participants.filter(participant => {
                      const biologicalRoles = participant.biological_roles?.filter(role => 
                        !role.name.toLowerCase().includes('unspecified') && 
                        !role.name.toLowerCase().includes('unknown')
                      ) || [];
                      const experimentalRoles = participant.experimental_roles?.filter(role => 
                        !role.name.toLowerCase().includes('unspecified') && 
                        !role.name.toLowerCase().includes('unknown')
                      ) || [];
                      const interactorTypes = participant.interactor_types?.filter(type => 
                        !type.name.toLowerCase().includes('unspecified') && 
                        !type.name.toLowerCase().includes('unknown')
                      ) || [];
                      
                      return biologicalRoles.length > 0 || experimentalRoles.length > 0 || interactorTypes.length > 0;
                    });

                    // Only show the participants section if there are participants with meaningful roles
                    if (participantsWithRoles.length === 0) return null;

                    return (
                      <div className="mb-4">
                        <div className="text-xs font-medium text-muted-foreground mb-2">Participants</div>
                        <div className="space-y-2">
                          {evidence.participants.map((participant, participantIndex) => {
                            // Filter out "unspecified role" and similar generic entries
                            const biologicalRoles = participant.biological_roles?.filter(role => 
                              !role.name.toLowerCase().includes('unspecified') && 
                              !role.name.toLowerCase().includes('unknown')
                            ) || [];
                            const experimentalRoles = participant.experimental_roles?.filter(role => 
                              !role.name.toLowerCase().includes('unspecified') && 
                              !role.name.toLowerCase().includes('unknown')
                            ) || [];
                            const interactorTypes = participant.interactor_types?.filter(type => 
                              !type.name.toLowerCase().includes('unspecified') && 
                              !type.name.toLowerCase().includes('unknown')
                            ) || [];

                            const hasRoles = biologicalRoles.length > 0 || experimentalRoles.length > 0 || interactorTypes.length > 0;
                            
                            // Only show participants that have meaningful roles
                            if (!hasRoles) return null;
                          
                            return (
                              <div key={participantIndex} className="border rounded-md p-2 bg-muted/20">
                                <div className="flex items-start gap-2 text-xs">
                                  <Badge variant="outline" className="text-xs font-medium flex-shrink-0">
                                    {participant.entity?.canonical_identifier}
                                    {participant.stoichiometry && ` (x${participant.stoichiometry})`}
                                  </Badge>
                                  <div className="flex flex-wrap gap-1 min-w-0">
                                    {biologicalRoles.map((role, roleIndex) => (
                                      <CvTermBadge 
                                        key={`bio-${roleIndex}`} 
                                        cvTermId={role.id}
                                        cvTermName={role.name}
                                        variant="secondary" 
                                        className="text-xs bg-green-50 text-green-700 border-green-200"
                                      />
                                    ))}
                                    {experimentalRoles.map((role, roleIndex) => (
                                      <CvTermBadge 
                                        key={`exp-${roleIndex}`} 
                                        cvTermId={role.id}
                                        cvTermName={role.name}
                                        variant="secondary" 
                                        className="text-xs bg-blue-50 text-blue-700 border-blue-200"
                                      />
                                    ))}
                                    {interactorTypes.map((type, typeIndex) => (
                                      <CvTermBadge 
                                        key={`type-${typeIndex}`} 
                                        cvTermId={type.id}
                                        cvTermName={type.name}
                                        variant="secondary" 
                                        className="text-xs bg-purple-50 text-purple-700 border-purple-200"
                                      />
                                    ))}
                                  </div>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })()}

                  {/* Reference */}
                  {evidence.reference?.pubmed_id && (
                    <div className="pt-3 border-t border-border/50">
                      <a
                        href={`https://pubmed.ncbi.nlm.nih.gov/${evidence.reference.pubmed_id}/`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
                      >
                        <ExternalLink className="h-3 w-3" />
                        PMID: {evidence.reference.pubmed_id}
                      </a>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </AccordionContent>
        </AccordionItem>



      </Accordion>
    </div>
  )
}

