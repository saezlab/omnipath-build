import { Badge } from "@/components/ui/badge"
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion"
import { FileText, Search, ArrowRight, Minus, Plus } from "lucide-react"
import { cn } from "@/lib/utils"
import { useMemo } from "react"
import { MeilisearchInteraction, InteractionEvidence, InteractionDirection } from "@/types/meilisearch"

interface InteractionDetailsProps {
  selectedInteraction: MeilisearchInteraction | null
}

// Helper function to extract label from "Label:ID" format
function extractLabel(value: string): string {
  const colonIndex = value.indexOf(':');
  return colonIndex > 0 ? value.substring(0, colonIndex) : value;
}

// Helper function to determine if members should be swapped based on direction
function shouldSwapMembers(directions: InteractionDirection[]): boolean {
  if (!directions || directions.length === 0) return false;
  return directions[0]?.direction === 'b-a';
}

// Helper to combine term with its value and unit
function formatAnnotationWithValue(
  terms: { value: string }[],
  values: { value: string }[],
  units: { value: string }[]
): { term: string; value?: string; unit?: string }[] {
  return terms.map((term, idx) => ({
    term: extractLabel(term.value),
    value: values[idx]?.value,
    unit: units[idx]?.value ? extractLabel(units[idx].value) : undefined,
  }));
}

// Helper to determine overall sign from directions
function getOverallSign(directions: InteractionDirection[]): 'positive' | 'negative' | 'mixed' | null {
  if (!directions || directions.length === 0) return null;

  const hasPositive = directions.some(d => d.sign === 1 || d.sign === 0);
  const hasNegative = directions.some(d => d.sign === -1 || d.sign === 0);

  if (hasPositive && hasNegative) return 'mixed';
  if (hasPositive) return 'positive';
  if (hasNegative) return 'negative';
  return null;
}

const getSignColor = (sign: 'positive' | 'negative' | 'mixed' | null) => {
  if (sign === 'positive') return 'text-green-600 bg-green-50 border-green-200';
  if (sign === 'negative') return 'text-red-600 bg-red-50 border-red-200';
  if (sign === 'mixed') return 'text-orange-600 bg-orange-50 border-orange-200';
  return 'text-gray-600 bg-gray-50 border-gray-200';
}

const getSignLabel = (sign: 'positive' | 'negative' | 'mixed' | null) => {
  if (sign === 'positive') return 'Activation';
  if (sign === 'negative') return 'Inhibition';
  if (sign === 'mixed') return 'Mixed';
  return 'Unknown';
}

// Extract all unique terms from evidence annotations
function extractAnnotationTerms(evidence: InteractionEvidence[]): string[] {
  const terms = new Set<string>();
  evidence.forEach(e => {
    e.interaction_annotation_terms?.forEach(t => terms.add(t.value));
    e.member_a_annotation_terms?.forEach(t => terms.add(t.value));
    e.member_b_annotation_terms?.forEach(t => terms.add(t.value));
  });
  return Array.from(terms);
}

export function InteractionDetails({ selectedInteraction }: InteractionDetailsProps) {
  const overallSign = useMemo(() => {
    if (!selectedInteraction) return null;
    return getOverallSign(selectedInteraction.directions);
  }, [selectedInteraction]);

  const getInteractionColor = () => {
    if (!selectedInteraction) return "text-gray-500";
    if (overallSign === 'positive') return "text-green-500";
    if (overallSign === 'negative') return "text-red-500";
    if (overallSign === 'mixed') return "text-orange-500";
    return "text-gray-500";
  }

  const evidenceStats = useMemo(() => {
    if (!selectedInteraction?.evidence) return null;

    const allTerms = extractAnnotationTerms(selectedInteraction.evidence);

    return {
      total: selectedInteraction.evidence.length,
      directions: selectedInteraction.directions.length,
      annotationTerms: allTerms.length,
    };
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

  const swap = shouldSwapMembers(selectedInteraction.directions);
  const sourceId = swap ? selectedInteraction.member_b_id : selectedInteraction.member_a_id;
  const targetId = swap ? selectedInteraction.member_a_id : selectedInteraction.member_b_id;
  const sourceType = swap
    ? (selectedInteraction.member_types[1] ? extractLabel(selectedInteraction.member_types[1]) : 'Unknown')
    : (selectedInteraction.member_types[0] ? extractLabel(selectedInteraction.member_types[0]) : 'Unknown');
  const targetType = swap
    ? (selectedInteraction.member_types[0] ? extractLabel(selectedInteraction.member_types[0]) : 'Unknown')
    : (selectedInteraction.member_types[1] ? extractLabel(selectedInteraction.member_types[1]) : 'Unknown');

  return (
    <div className="p-4 pb-8 space-y-6">
      {/* ===== INTERACTION OVERVIEW ===== */}
      <div className="rounded-lg border bg-card p-6">
        {/* Entity Visualization */}
        <div className="flex items-center justify-center gap-6 py-6">
          <div className="flex flex-col items-center">
            <span className="font-bold text-lg">{sourceId}</span>
            <Badge variant="secondary" className="text-xs mt-1">{sourceType}</Badge>
          </div>

          <div className="flex flex-col items-center gap-2">
            <div className={cn("flex items-center", getInteractionColor())}>
              {selectedInteraction.has_direction ? (
                <ArrowRight className="h-8 w-8" />
              ) : (
                <Minus className="h-8 w-8" />
              )}
            </div>
            {overallSign && (
              <Badge className={cn("text-xs px-2 py-1 border", getSignColor(overallSign))}>
                {overallSign === 'positive' && <Plus className="h-3 w-3 mr-1" />}
                {overallSign === 'negative' && <Minus className="h-3 w-3 mr-1" />}
                {getSignLabel(overallSign)}
              </Badge>
            )}
          </div>

          <div className="flex flex-col items-center">
            <span className="font-bold text-lg">{targetId}</span>
            <Badge variant="secondary" className="text-xs mt-1">{targetType}</Badge>
          </div>
        </div>

        {/* Evidence Summary Stats */}
        {evidenceStats && (
          <div className="grid grid-cols-3 gap-4 pt-4 border-t">
            <div className="text-center">
              <div className="text-2xl font-bold text-primary">{evidenceStats.total}</div>
              <div className="text-xs text-muted-foreground">Evidence{evidenceStats.total !== 1 ? 's' : ''}</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-blue-600">{evidenceStats.directions}</div>
              <div className="text-xs text-muted-foreground">Direction{evidenceStats.directions !== 1 ? 's' : ''}</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-purple-600">{evidenceStats.annotationTerms}</div>
              <div className="text-xs text-muted-foreground">Annotation Term{evidenceStats.annotationTerms !== 1 ? 's' : ''}</div>
            </div>
          </div>
        )}
      </div>

      {/* ===== PROGRESSIVE DISCLOSURE ACCORDIONS ===== */}
      <Accordion type="multiple" defaultValue={["directions", "evidence"]} className="space-y-4">

        {/* Directions Section */}
        {selectedInteraction.directions.length > 0 && (
          <AccordionItem value="directions" className="border rounded-lg">
            <AccordionTrigger className="px-4 py-3 hover:no-underline">
              <div className="flex items-center gap-2">
                <ArrowRight className="h-5 w-5" />
                <span className="font-medium">Directions & Signs</span>
                <Badge variant="secondary" className="ml-2">
                  {selectedInteraction.directions.length}
                </Badge>
              </div>
            </AccordionTrigger>
            <AccordionContent className="px-4 pb-4">
              <div className="space-y-2">
                {selectedInteraction.directions.map((dir, index) => (
                  <div key={index} className="flex items-center gap-3 p-3 border rounded-lg bg-muted/30">
                    <Badge variant="outline" className="text-xs">
                      {dir.direction === 'a-b' ? `${selectedInteraction.member_a_id} → ${selectedInteraction.member_b_id}` : `${selectedInteraction.member_b_id} → ${selectedInteraction.member_a_id}`}
                    </Badge>
                    <Badge
                      className={cn(
                        "text-xs",
                        dir.sign === 1 && "text-green-600 bg-green-50 border-green-200",
                        dir.sign === -1 && "text-red-600 bg-red-50 border-red-200",
                        dir.sign === 0 && "text-orange-600 bg-orange-50 border-orange-200"
                      )}
                    >
                      {dir.sign === 1 && <Plus className="h-3 w-3 mr-1" />}
                      {dir.sign === -1 && <Minus className="h-3 w-3 mr-1" />}
                      {dir.sign === 1 ? 'Activation' : dir.sign === -1 ? 'Inhibition' : 'Mixed'}
                    </Badge>
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
                {selectedInteraction.evidence?.length || 0}
              </Badge>
            </div>
          </AccordionTrigger>
          <AccordionContent className="px-4 pb-4">
            <div className="space-y-6">
              {selectedInteraction.evidence?.map((evidence, index) => (
                <div key={index} className="border rounded-lg p-4 bg-muted/30">
                  {/* Evidence Header */}
                  <div className="flex items-start justify-between mb-3">
                    <Badge variant="outline" className="text-xs">
                      Evidence {index + 1}
                    </Badge>
                  </div>

                  {/* Interaction Annotations */}
                  {evidence.interaction_annotation_terms && evidence.interaction_annotation_terms.length > 0 && (
                    <div className="mb-4">
                      <div className="text-xs font-medium text-muted-foreground mb-2">Interaction Annotations</div>
                      <div className="flex flex-wrap gap-2">
                        {formatAnnotationWithValue(
                          evidence.interaction_annotation_terms,
                          evidence.interaction_annotation_values || [],
                          evidence.interaction_annotation_units || []
                        ).map((annotation, termIdx) => (
                          <Badge key={termIdx} variant="secondary" className="text-xs">
                            {annotation.term}
                            {annotation.value && (
                              <span className="ml-1 font-normal opacity-80">
                                = {annotation.value}{annotation.unit && ` ${annotation.unit}`}
                              </span>
                            )}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Source Member Annotations */}
                  {(() => {
                    const sourceTerms = swap ? evidence.member_b_annotation_terms : evidence.member_a_annotation_terms;
                    const sourceValues = swap ? evidence.member_b_annotation_values : evidence.member_a_annotation_values;
                    const sourceUnits = swap ? evidence.member_b_annotation_units : evidence.member_a_annotation_units;
                    return sourceTerms && sourceTerms.length > 0 ? (
                      <div className="mb-4">
                        <div className="text-xs font-medium text-muted-foreground mb-2">
                          Source ({sourceId}) Annotations
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {formatAnnotationWithValue(
                            sourceTerms,
                            sourceValues || [],
                            sourceUnits || []
                          ).map((annotation, termIdx) => (
                            <Badge key={termIdx} variant="outline" className="text-xs bg-blue-50 text-blue-700 border-blue-200">
                              {annotation.term}
                              {annotation.value && (
                                <span className="ml-1 font-normal opacity-80">
                                  = {annotation.value}{annotation.unit && ` ${annotation.unit}`}
                                </span>
                              )}
                            </Badge>
                          ))}
                        </div>
                      </div>
                    ) : null;
                  })()}

                  {/* Target Member Annotations */}
                  {(() => {
                    const targetTerms = swap ? evidence.member_a_annotation_terms : evidence.member_b_annotation_terms;
                    const targetValues = swap ? evidence.member_a_annotation_values : evidence.member_b_annotation_values;
                    const targetUnits = swap ? evidence.member_a_annotation_units : evidence.member_b_annotation_units;
                    return targetTerms && targetTerms.length > 0 ? (
                      <div className="mb-4">
                        <div className="text-xs font-medium text-muted-foreground mb-2">
                          Target ({targetId}) Annotations
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {formatAnnotationWithValue(
                            targetTerms,
                            targetValues || [],
                            targetUnits || []
                          ).map((annotation, termIdx) => (
                            <Badge key={termIdx} variant="outline" className="text-xs bg-purple-50 text-purple-700 border-purple-200">
                              {annotation.term}
                              {annotation.value && (
                                <span className="ml-1 font-normal opacity-80">
                                  = {annotation.value}{annotation.unit && ` ${annotation.unit}`}
                                </span>
                              )}
                            </Badge>
                          ))}
                        </div>
                      </div>
                    ) : null;
                  })()}
                </div>
              ))}
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* Annotation Terms Summary */}
        {selectedInteraction.interaction_annotation_terms && selectedInteraction.interaction_annotation_terms.length > 0 && (
          <AccordionItem value="annotation_terms" className="border rounded-lg">
            <AccordionTrigger className="px-4 py-3 hover:no-underline">
              <div className="flex items-center gap-2">
                <FileText className="h-5 w-5" />
                <span className="font-medium">All Annotation Terms</span>
                <Badge variant="secondary" className="ml-2">
                  {selectedInteraction.interaction_annotation_terms.length}
                </Badge>
              </div>
            </AccordionTrigger>
            <AccordionContent className="px-4 pb-4">
              <div className="flex flex-wrap gap-2">
                {selectedInteraction.interaction_annotation_terms.map((term, idx) => (
                  <Badge key={idx} variant="secondary" className="text-xs">
                    {extractLabel(term)}
                  </Badge>
                ))}
              </div>
            </AccordionContent>
          </AccordionItem>
        )}
      </Accordion>
    </div>
  )
}
