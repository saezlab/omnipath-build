import { useState, useEffect } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Check, Edit2, X, Hash, Database, Loader2 } from 'lucide-react';
import { ScrollArea } from '@/components/ui/scroll-area';
import { FieldMapping, TRANSFORM_FUNCTIONS, loadTransformFunctions } from '../types';
import { previewTransformation } from '../api/preview-transformation';

interface TargetFieldCardProps {
  field: {
    name: string;
    required: boolean;
    description: string;
  };
  mapping?: FieldMapping;
  sourceColumns: string[];
  isEditing: boolean;
  onEdit: () => void;
  onUpdate: (mapping: FieldMapping | null) => void;
  onCancel: () => void;
  datasourceId?: string;
  datasetName?: string;
  targetModel?: string;
}

export function TargetFieldCard({
  field,
  mapping,
  sourceColumns,
  isEditing,
  onEdit,
  onUpdate,
  onCancel,
  datasourceId,
  datasetName,
  targetModel
}: TargetFieldCardProps) {
  // Initialize state from existing mapping
  const [inputType, setInputType] = useState<'column' | 'constant'>(
    mapping?.constantValue ? 'constant' : 'column'
  );
  const [selectedColumn, setSelectedColumn] = useState(mapping?.sourceColumn || '');
  const [constantValue, setConstantValue] = useState(mapping?.constantValue || '');
  const [transform, setTransform] = useState(mapping?.transform || 'none');
  const [transformArgs, setTransformArgs] = useState(mapping?.transformArgs || {});
  
  // Dynamic preview state
  const [previewData, setPreviewData] = useState<{
    rows: Array<{ input: string; output: string; }>;
    totalCount: number;
  } | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);

  // Load transformation functions on mount
  useEffect(() => {
    loadTransformFunctions();
  }, []);

  // Update state when mapping changes (e.g., when loading existing config)
  useEffect(() => {
    if (mapping) {
      setInputType(mapping.constantValue ? 'constant' : 'column');
      setSelectedColumn(mapping.sourceColumn || '');
      setConstantValue(mapping.constantValue || '');
      setTransform(mapping.transform || 'none');
      setTransformArgs(mapping.transformArgs || {});
    }
  }, [mapping]);

  const selectedTransform = TRANSFORM_FUNCTIONS.find(t => t.name === transform);

  // Load dynamic preview when configuration changes
  useEffect(() => {
    async function loadPreview() {
      if (!datasourceId || !datasetName || !targetModel || !isEditing) {
        setPreviewData(null);
        return;
      }

      // Only show preview for column mappings with transformations or when editing
      if (inputType === 'constant' || !selectedColumn) {
        setPreviewData(null);
        return;
      }

      setIsLoadingPreview(true);
      try {
        // Create a temporary mapping config for this single field
        const tempMappingConfig = {
          targetModel: targetModel as 'interactions' | 'entities' | 'controlled_vocabularies',
          mappings: {
            [field.name]: {
              targetField: field.name,
              sourceColumn: selectedColumn,
              transform: transform === 'none' ? undefined : transform,
              transformArgs: selectedTransform?.requiresArgs ? transformArgs : undefined
            }
          }
        };

        const result = await previewTransformation(datasourceId, datasetName, tempMappingConfig);
        
        if (result.success && result.sourceData && result.transformedData && result.sourceData.length > 0) {
          // Create rows from all available data
          const rows = result.sourceData.map((sourceRow, index) => {
            const sourceValue = sourceRow[selectedColumn];
            const transformedValue = result.transformedData?.[index]?.[field.name];
            
            return {
              input: sourceValue !== null ? String(sourceValue) : 'null',
              output: transformedValue !== null ? String(transformedValue) : 'null'
            };
          });
          
          setPreviewData({
            rows,
            totalCount: result.totalRows || 0
          });
        } else {
          // Fallback to static examples if dynamic preview fails
          const exampleInput = getExampleInput(selectedColumn);
          const exampleOutput = getExampleOutput(
            exampleInput,
            transform === 'none' ? undefined : transform,
            transformArgs
          );
          
          setPreviewData({
            rows: [{ input: exampleInput, output: exampleOutput }],
            totalCount: 1
          });
        }
      } catch (_error) {
        console.error('Error loading dynamic preview:', _error);
        // Fallback to static examples
        const exampleInput = getExampleInput(selectedColumn);
        const exampleOutput = getExampleOutput(
          exampleInput,
          transform === 'none' ? undefined : transform,
          transformArgs
        );
        
        setPreviewData({
          rows: [{ input: exampleInput, output: exampleOutput }],
          totalCount: 1
        });
      } finally {
        setIsLoadingPreview(false);
      }
    }

    // Debounce the preview loading
    const timeoutId = setTimeout(loadPreview, 500);
    return () => clearTimeout(timeoutId);
  }, [datasourceId, datasetName, targetModel, isEditing, inputType, selectedColumn, transform, transformArgs, field.name, selectedTransform?.requiresArgs]);

  const handleSave = () => {
    if (inputType === 'constant' && constantValue) {
      onUpdate({
        targetField: field.name,
        constantValue
      });
    } else if (inputType === 'column' && selectedColumn) {
      onUpdate({
        targetField: field.name,
        sourceColumn: selectedColumn,
        transform: transform === 'none' ? undefined : transform,
        transformArgs: selectedTransform?.requiresArgs ? transformArgs : undefined
      });
    }
  };

  const handleClear = () => {
    onUpdate(null);
  };

  if (!isEditing) {
    return (
      <Card 
        className={`cursor-pointer transition-all hover:shadow-md ${
          field.required && !mapping 
            ? 'border-orange-200 dark:border-orange-900' 
            : mapping
            ? 'border-green-200 dark:border-green-900'
            : ''
        }`}
        onClick={onEdit}
      >
        <CardContent className="p-4">
          <div className="flex items-start justify-between">
            <div className="flex-1">
              <div className="flex items-center gap-2 mb-1">
                <h3 className="font-medium">{field.name}</h3>
                {field.required && (
                  <Badge variant="outline" className="text-xs">Required</Badge>
                )}
                {mapping && (
                  <Check className="w-4 h-4 text-green-600" />
                )}
              </div>
              <p className="text-sm text-muted-foreground mb-2">{field.description}</p>
              
              {mapping && (
                <div className="flex items-center gap-2 mt-3">
                  {mapping.constantValue ? (
                    <>
                      <Hash className="w-4 h-4 text-muted-foreground" />
                      <code className="text-sm bg-muted px-2 py-1 rounded">
                        &quot;{mapping.constantValue}&quot;
                      </code>
                    </>
                  ) : (
                    <>
                      <Database className="w-4 h-4 text-muted-foreground" />
                      <code className="text-sm bg-muted px-2 py-1 rounded">
                        {mapping.sourceColumn}
                      </code>
                      {mapping.transform && (
                        <>
                          <span className="text-muted-foreground">→</span>
                          <Badge variant="secondary" className="text-xs">
                            {mapping.transform}
                          </Badge>
                        </>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
            <Edit2 className="w-4 h-4 text-muted-foreground" />
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-primary">
      <CardContent className="p-4 space-y-4">
        <div className="flex items-start justify-between">
          <div>
            <h3 className="font-medium">{field.name}</h3>
            <p className="text-sm text-muted-foreground">{field.description}</p>
          </div>
          <Button
            variant="ghost"
            size="icon"
            onClick={onCancel}
          >
            <X className="w-4 h-4" />
          </Button>
        </div>

        <RadioGroup value={inputType} onValueChange={(v) => setInputType(v as 'column' | 'constant')}>
          <div className="flex items-center space-x-2">
            <RadioGroupItem value="column" id={`${field.name}-column`} />
            <Label htmlFor={`${field.name}-column`}>Map from source column</Label>
          </div>
          <div className="flex items-center space-x-2">
            <RadioGroupItem value="constant" id={`${field.name}-constant`} />
            <Label htmlFor={`${field.name}-constant`}>Use constant value</Label>
          </div>
        </RadioGroup>

        {inputType === 'column' && (
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Source Column</Label>
              <Select value={selectedColumn} onValueChange={setSelectedColumn}>
                <SelectTrigger>
                  <SelectValue placeholder="Select a source column" />
                </SelectTrigger>
                <SelectContent>
                  {sourceColumns.map((col) => (
                    <SelectItem key={col} value={col}>
                      {col}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label>Transformation (Optional)</Label>
              <Select value={transform} onValueChange={setTransform}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">No transformation</SelectItem>
                  {TRANSFORM_FUNCTIONS.map((fn) => (
                    <SelectItem key={fn.name} value={fn.name}>
                      <div>
                        <div className="font-medium">{fn.name}</div>
                        <div className="text-xs text-muted-foreground">{fn.description}</div>
                      </div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {selectedTransform?.requiresArgs && selectedTransform.argSchema && (
              <div className="space-y-2 p-3 border rounded-lg bg-muted/50">
                <Label className="text-sm">Transformation Arguments</Label>
                {Object.entries(selectedTransform.argSchema).map(([argName, argDef]) => (
                  <div key={argName} className="space-y-1">
                    <Label className="text-xs">
                      {argName.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}
                    </Label>
                    <Input
                      value={String(transformArgs[argName] || '')}
                      onChange={(e) => setTransformArgs({
                        ...transformArgs,
                        [argName]: e.target.value
                      })}
                      placeholder={(argDef as { description?: string })?.description || ''}
                      className="h-8"
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {inputType === 'constant' && (
          <div className="space-y-2">
            <Label>Constant Value</Label>
            <Input
              value={constantValue}
              onChange={(e) => setConstantValue(e.target.value)}
              placeholder="Enter constant value"
            />
          </div>
        )}

        {/* Dynamic Preview Table */}
        {((inputType === 'column' && selectedColumn) || (inputType === 'constant' && constantValue)) && (
          <div className="space-y-2 p-3 border rounded-lg bg-muted/50">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Label className="text-sm">Transformation Preview</Label>
                {isLoadingPreview && (
                  <Loader2 className="w-3 h-3 animate-spin text-muted-foreground" />
                )}
                {previewData && !isLoadingPreview && datasourceId && (
                  <Badge variant="outline" className="text-xs">Real Data</Badge>
                )}
              </div>
              {previewData && previewData.totalCount > previewData.rows.length && (
                <span className="text-xs text-muted-foreground">
                  Showing {previewData.rows.length} of {previewData.totalCount.toLocaleString()} rows
                </span>
              )}
            </div>
            
            {inputType === 'constant' ? (
              <div className="space-y-1 font-mono text-xs">
                <div className="flex items-start gap-2">
                  <span className="text-muted-foreground">Constant Value:</span>
                  <span className="text-primary break-all">{constantValue}</span>
                </div>
              </div>
            ) : previewData?.rows && previewData.rows.length > 0 ? (
              <ScrollArea className="h-48 w-full">
                <div className="border rounded-lg overflow-hidden">
                  <table className="w-full text-xs">
                    <thead className="bg-muted/50 sticky top-0">
                      <tr>
                        <th className="px-3 py-2 text-left font-medium border-r">
                          Source: {selectedColumn}
                        </th>
                        <th className="px-3 py-2 text-left font-medium">
                          Target: {field.name}
                          {transform !== 'none' && (
                            <span className="ml-1 text-primary">({transform})</span>
                          )}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {previewData.rows.map((row, index) => (
                        <tr key={index} className="border-t hover:bg-muted/30">
                          <td className="px-3 py-2 font-mono border-r max-w-[200px]">
                            <div className="truncate" title={row.input}>
                              {row.input === 'null' ? (
                                <span className="text-muted-foreground italic">null</span>
                              ) : (
                                row.input
                              )}
                            </div>
                          </td>
                          <td className="px-3 py-2 font-mono text-primary max-w-[200px]">
                            <div className="truncate" title={row.output}>
                              {row.output === 'null' ? (
                                <span className="text-muted-foreground italic">null</span>
                              ) : (
                                row.output
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </ScrollArea>
            ) : (
              <div className="text-center text-muted-foreground py-4 text-xs">
                {isLoadingPreview ? 'Loading preview...' : 'No preview data available'}
              </div>
            )}
          </div>
        )}

        <div className="flex items-center justify-end gap-2 pt-2">
          {mapping && (
            <Button
              variant="ghost"
              size="sm"
              onClick={handleClear}
              className="text-destructive"
            >
              Clear
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={onCancel}
          >
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={handleSave}
            disabled={
              (inputType === 'column' && !selectedColumn) ||
              (inputType === 'constant' && !constantValue)
            }
          >
            Save
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function getExampleInput(columnName: string): string {
  // Provide realistic example data based on column name patterns
  const lowerColumn = columnName.toLowerCase();
  
  if (lowerColumn.includes('id') && lowerColumn.includes('interactor')) {
    return 'uniprot:P12345';
  } else if (lowerColumn.includes('type') && lowerColumn.includes('interactor')) {
    return 'protein';
  } else if (lowerColumn.includes('publication')) {
    return 'pubmed:12345678';
  } else if (lowerColumn.includes('interaction type')) {
    return 'direct interaction';
  } else if (lowerColumn.includes('detection method')) {
    return 'two hybrid';
  } else if (lowerColumn.includes('causal')) {
    return 'up-regulates';
  } else if (lowerColumn.includes('annotation')) {
    return 'Phosphorylation of S102 by PKA';
  } else if (lowerColumn.includes('identifier')) {
    return 'SIGNOR-12345';
  } else if (lowerColumn.includes('signor id')) {
    return 'SIGNOR:P12345';
  } else if (lowerColumn.includes('complex name') || lowerColumn.includes('family name')) {
    return 'NF-kB complex';
  } else if (lowerColumn.includes('list of entities')) {
    return 'P19838;P23511;Q04206';
  } else if (lowerColumn.includes('description')) {
    return 'Nuclear factor kappa B transcription complex';
  }
  
  return 'example_value';
}

function getExampleOutput(input: string, transform?: string, transformArgs?: Record<string, unknown>): string {
  if (!transform) {
    return input;
  }

  switch (transform) {
    case 'extract_identifier':
      // Extract ID after colon
      if (input.includes(':')) {
        return input.split(':')[1];
      }
      return input;
      
    case 'map_identifier_type_to_mi':
      // Map identifier type to MI term
      if (input.toLowerCase().includes('uniprot')) {
        return 'MI:0486';
      } else if (input.toLowerCase().includes('pubmed')) {
        return 'MI:0446';
      }
      return 'MI:0000';
      
    case 'extract_mi_term':
      // Convert text to MI term
      if (input.toLowerCase().includes('direct')) {
        return 'MI:0407';
      } else if (input.toLowerCase().includes('two hybrid')) {
        return 'MI:0018';
      }
      return 'MI:0000';
      
    case 'extract_pubmed_id':
      // Extract numeric PubMed ID
      const pubmedMatch = input.match(/\d{6,9}/);
      return pubmedMatch ? pubmedMatch[0] : '12345678';
      
    case 'extract_accession':
      // Extract accession number
      if (input.includes(':')) {
        return input.split(':')[1];
      } else if (input.includes('-')) {
        return input.split('-')[1];
      }
      return input;
      
    case 'format_alt_identifier':
      // Format with ID type
      const idType = transformArgs?.id_type || 'OM00001';
      return `${idType}:${input}`;
      
    case 'parse_signor_members':
      // Convert semicolon list to array format
      return `[${input.split(';').map(id => `"${id.trim()}"`).join(', ')}]`;
      
    case 'infer_signor_entity_type':
      // Infer type from SIGNOR ID pattern
      if (input.includes('SIGNOR:P')) {
        return 'protein';
      } else if (input.includes('SIGNOR:C')) {
        return 'complex';
      } else if (input.includes('SIGNOR:PF')) {
        return 'protein_family';
      }
      return 'entity';
      
    case 'extract_evidence_sentence':
      // Clean up evidence text
      return input.replace(/\s+/g, ' ').trim();
      
    default:
      return input;
  }
}