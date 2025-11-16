import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import { AlertCircle, CheckCircle, XCircle, Loader2 } from 'lucide-react';
import { useState, useEffect } from 'react';
import { DataProcessingConfig } from '../types';
import { previewTransformation, TransformationPreviewResult } from '../api/preview-transformation';

interface PreviewPanelProps {
  mappingConfig: DataProcessingConfig;
  datasourceId: string;
  datasetName: string;
  onClose: () => void;
}

export function PreviewPanel({ mappingConfig, datasourceId, datasetName, onClose }: PreviewPanelProps) {
  const [previewResult, setPreviewResult] = useState<TransformationPreviewResult | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function loadPreview() {
      if (Object.keys(mappingConfig.mappings).length === 0) {
        setPreviewResult({
          success: false,
          errors: ['No field mappings configured']
        });
        setIsLoading(false);
        return;
      }

      setIsLoading(true);
      try {
        const result = await previewTransformation(datasourceId, datasetName, mappingConfig);
        setPreviewResult(result);
      } catch (error) {
        setPreviewResult({
          success: false,
          errors: [`Failed to load preview: ${error instanceof Error ? error.message : 'Unknown error'}`]
        });
      } finally {
        setIsLoading(false);
      }
    }

    loadPreview();
  }, [mappingConfig, datasourceId, datasetName]);

  // Calculate validation stats
  const validRowCount = previewResult?.transformedData?.length || 0;
  const warningCount = previewResult?.warnings?.length || 0;
  const errorCount = previewResult?.errors?.length || 0;

  return (
    <Dialog open onOpenChange={onClose}>
      <DialogContent className="max-w-6xl max-h-[80vh]">
        <DialogHeader>
          <DialogTitle>Mapping Preview</DialogTitle>
        </DialogHeader>
        
        <Tabs defaultValue="transformed" className="w-full">
          <TabsList className="grid w-full grid-cols-4">
            <TabsTrigger value="transformed">Transformed Data</TabsTrigger>
            <TabsTrigger value="source">Source Data</TabsTrigger>
            <TabsTrigger value="validation">Validation</TabsTrigger>
            <TabsTrigger value="summary">Summary</TabsTrigger>
          </TabsList>
          
          <TabsContent value="transformed" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">
                  Transformed Data Preview
                  {previewResult?.totalRows && (
                    <span className="ml-2 text-sm font-normal text-muted-foreground">
                      (showing {previewResult.transformedData?.length || 0} of {previewResult.totalRows.toLocaleString()} rows)
                    </span>
                  )}
                </CardTitle>
              </CardHeader>
              <CardContent>
                {isLoading ? (
                  <div className="flex items-center justify-center h-32">
                    <Loader2 className="w-6 h-6 animate-spin" />
                    <span className="ml-2">Applying transformations...</span>
                  </div>
                ) : !previewResult?.success ? (
                  <div className="flex items-center gap-2 p-4 border rounded-lg bg-red-50 dark:bg-red-950/20">
                    <XCircle className="w-5 h-5 text-red-600" />
                    <div>
                      <div className="font-medium">Transformation Failed</div>
                      {previewResult?.errors?.map((error, idx) => (
                        <div key={idx} className="text-sm text-muted-foreground">{error}</div>
                      ))}
                    </div>
                  </div>
                ) : previewResult.transformedData && previewResult.transformedData.length > 0 ? (
                  <ScrollArea className="h-[400px]">
                    <div className="border rounded-lg overflow-hidden">
                      <table className="w-full text-sm">
                        <thead className="bg-muted/50 sticky top-0">
                          <tr>
                            {Object.keys(previewResult.transformedData[0]).map((key) => (
                              <th key={key} className="px-4 py-2 text-left font-medium">
                                {key}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {previewResult.transformedData.map((row, idx) => (
                            <tr key={idx} className="border-t hover:bg-muted/50">
                              {Object.values(row).map((value, vidx) => (
                                <td key={vidx} className="px-4 py-2 font-mono text-xs">
                                  {value === null ? (
                                    <span className="text-muted-foreground italic">null</span>
                                  ) : (
                                    String(value)
                                  )}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </ScrollArea>
                ) : (
                  <div className="text-center text-muted-foreground py-8">
                    No transformed data available
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>
          
          <TabsContent value="source" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Source Data Sample</CardTitle>
              </CardHeader>
              <CardContent>
                {isLoading ? (
                  <div className="flex items-center justify-center h-32">
                    <Loader2 className="w-6 h-6 animate-spin" />
                    <span className="ml-2">Loading source data...</span>
                  </div>
                ) : previewResult?.sourceData && previewResult.sourceData.length > 0 ? (
                  <ScrollArea className="h-[400px]">
                    <div className="border rounded-lg overflow-hidden">
                      <table className="w-full text-sm">
                        <thead className="bg-muted/50 sticky top-0">
                          <tr>
                            {Object.keys(previewResult.sourceData[0]).map((key) => (
                              <th key={key} className="px-4 py-2 text-left font-medium">
                                {key}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {previewResult.sourceData.map((row, idx) => (
                            <tr key={idx} className="border-t hover:bg-muted/50">
                              {Object.values(row).map((value, vidx) => (
                                <td key={vidx} className="px-4 py-2 font-mono text-xs">
                                  {value === null ? (
                                    <span className="text-muted-foreground italic">null</span>
                                  ) : (
                                    String(value)
                                  )}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </ScrollArea>
                ) : (
                  <div className="text-center text-muted-foreground py-8">
                    No source data available
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>
          
          <TabsContent value="validation" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Validation Results</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  {/* Summary Stats */}
                  <div className="grid grid-cols-3 gap-4">
                    <div className="flex items-center gap-2 p-3 border rounded-lg">
                      <CheckCircle className="w-5 h-5 text-green-600" />
                      <div>
                        <div className="font-semibold">{validRowCount}</div>
                        <div className="text-sm text-muted-foreground">Valid Rows</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 p-3 border rounded-lg">
                      <AlertCircle className="w-5 h-5 text-orange-600" />
                      <div>
                        <div className="font-semibold">{warningCount}</div>
                        <div className="text-sm text-muted-foreground">Warnings</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 p-3 border rounded-lg">
                      <XCircle className="w-5 h-5 text-red-600" />
                      <div>
                        <div className="font-semibold">{errorCount}</div>
                        <div className="text-sm text-muted-foreground">Errors</div>
                      </div>
                    </div>
                  </div>

                  {/* Issues List */}
                  {(previewResult?.warnings?.length || previewResult?.errors?.length) ? (
                    <div className="space-y-2">
                      <h4 className="font-medium">Validation Issues</h4>
                      <div className="space-y-2">
                        {previewResult?.errors?.map((error, idx) => (
                          <div key={`error-${idx}`} className="flex items-center gap-2 p-2 border rounded-lg bg-red-50 dark:bg-red-950/20">
                            <XCircle className="w-4 h-4 text-red-600 flex-shrink-0" />
                            <div className="flex-1 text-sm">
                              <span className="font-medium">Error:</span> {error}
                            </div>
                          </div>
                        ))}
                        {previewResult?.warnings?.map((warning, idx) => (
                          <div key={`warning-${idx}`} className="flex items-center gap-2 p-2 border rounded-lg bg-orange-50 dark:bg-orange-950/20">
                            <AlertCircle className="w-4 h-4 text-orange-600 flex-shrink-0" />
                            <div className="flex-1 text-sm">
                              <span className="font-medium">Warning:</span> {warning}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div className="text-center text-muted-foreground py-4">
                      {isLoading ? "Validating..." : "No validation issues found"}
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          </TabsContent>
          
          <TabsContent value="summary" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Mapping Summary</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  <div>
                    <h4 className="font-medium mb-2">Target Model</h4>
                    <Badge variant="outline" className="capitalize">
                      {mappingConfig.targetModel.replace('_', ' ')}
                    </Badge>
                  </div>
                  
                  <div>
                    <h4 className="font-medium mb-2">Field Mappings</h4>
                    <div className="space-y-2">
                      {Object.entries(mappingConfig.mappings).map(([field, mapping]) => (
                        <div key={field} className="flex items-center justify-between p-2 border rounded-lg">
                          <div className="font-mono text-sm">{field}</div>
                          <div className="flex items-center gap-2">
                            {mapping.constantValue ? (
                              <Badge variant="secondary">Constant: {mapping.constantValue}</Badge>
                            ) : (
                              <>
                                <span className="text-sm text-muted-foreground">←</span>
                                <span className="font-mono text-sm">{mapping.sourceColumn}</span>
                              </>
                            )}
                            {mapping.transform && (
                              <Badge variant="outline" className="text-xs">
                                {mapping.transform}
                              </Badge>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                  
                  <div>
                    <h4 className="font-medium mb-2">Statistics</h4>
                    <div className="grid grid-cols-2 gap-2 text-sm">
                      <div>Total source rows:</div>
                      <div className="font-mono">{previewResult?.totalRows || 0}</div>
                      <div>Preview rows:</div>
                      <div className="font-mono">{previewResult?.sourceData?.length || 0}</div>
                      <div>Mapped columns:</div>
                      <div className="font-mono">{Object.keys(mappingConfig.mappings).length}</div>
                      <div>Transformations applied:</div>
                      <div className="font-mono">
                        {Object.values(mappingConfig.mappings).filter(m => m.transform).length}
                      </div>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}