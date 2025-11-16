"use client";

import { useState, useEffect } from 'react';
import { ArrowLeft, Save, Play } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useRouter } from 'next/navigation';
import { DataSource, Dataset } from '../../types/datasource';
import { DataProcessingConfig, TargetModel, getFieldsForModel, FieldMapping } from '../types';

interface ProcessingValue {
  constant_value?: string;
  source?: string;
  transform?: string;
  transform_args?: Record<string, unknown>;
}
import { toast } from 'sonner';
import { TargetFieldCard } from './target-field-card';
import { PreviewPanel } from './preview-panel';
import { saveMappingConfiguration, loadMappingConfiguration } from '../api/mapping-mutations';

interface MappingConfigurationProps {
  datasource: DataSource;
  dataset: Dataset;
}

export function MappingConfiguration({ datasource, dataset }: MappingConfigurationProps) {
  const router = useRouter();
  const [targetModel, setTargetModel] = useState<TargetModel>('interactions');
  const [mappingConfig, setMappingConfig] = useState<DataProcessingConfig>({
    targetModel,
    mappings: {}
  });
  const [sourceColumns, setSourceColumns] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  const [editingField, setEditingField] = useState<string | null>(null);

  // Load existing configuration
  useEffect(() => {
    const loadConfig = async () => {
      setIsLoading(true);
      try {
        // First check if dataset already has data_processing config
        if (dataset.dataProcessing && typeof dataset.dataProcessing === 'object') {
          const processing = dataset.dataProcessing as Record<string, unknown>;
          const targetModel = processing.target_model || 'interactions';
          
          // Convert from database format to our format
          const mappings: Record<string, FieldMapping> = {};
          Object.entries(processing).forEach(([key, value]) => {
            if (key === 'target_model') return;
            
            if (typeof value === 'object' && value !== null) {
              if ('constant_value' in value && value.constant_value !== undefined) {
                mappings[key] = {
                  targetField: key,
                  constantValue: (value as ProcessingValue).constant_value
                };
              } else if ('source' in value) {
                mappings[key] = {
                  targetField: key,
                  sourceColumn: (value as ProcessingValue).source,
                  transform: (value as ProcessingValue).transform,
                  transformArgs: (value as ProcessingValue).transform_args
                };
              }
            }
          });

          setMappingConfig({
            targetModel: targetModel as TargetModel,
            mappings
          });
          setTargetModel(targetModel as TargetModel);
        } else {
          // Try loading from database
          const config = await loadMappingConfiguration(datasource.id, dataset.name);
          if (config) {
            setMappingConfig(config);
            setTargetModel(config.targetModel);
          }
        }
      } catch (error) {
        console.error('Error loading configuration:', error);
      } finally {
        setIsLoading(false);
      }
    };
    
    loadConfig();
  }, [datasource.id, dataset.name, dataset.dataProcessing]);

  // Load actual source columns from database
  useEffect(() => {
    const loadSourceColumns = async () => {
      try {
        // Import the API function
        const { getTableStats } = await import('../../../data-transparency/api/bronze-queries');
        
        // Use the bronze schema with the correct table naming pattern from migration
        // Pattern: {resource_id}__{dataset_name} (double underscore)
        const bronzeTableName = `${datasource.id}__${dataset.name}`.replace(/-/g, '_').replace(/ /g, '_');
        const schema = 'bronze';
        
        const tableStats = await getTableStats(schema, bronzeTableName);
        const columnNames = tableStats.columns.map(col => col.name);
        setSourceColumns(columnNames);
      } catch (error) {
        console.error('Error loading source columns:', error);
        // Fallback to empty array if we can't load columns
        setSourceColumns([]);
      }
    };

    loadSourceColumns();
  }, [datasource.id, dataset.name]);

  const handleSave = async () => {
    setIsLoading(true);
    try {
      await saveMappingConfiguration(datasource.id, dataset.name, mappingConfig);
      toast.success('Mapping configuration saved successfully');
      router.back();
    } catch {
      toast.error('Failed to save mapping configuration');
    } finally {
      setIsLoading(false);
    }
  };

  const handleFieldUpdate = (fieldName: string, mapping: FieldMapping | null) => {
    if (mapping === null) {
      // Remove mapping
      const newMappings = { ...mappingConfig.mappings };
      delete newMappings[fieldName];
      setMappingConfig({
        ...mappingConfig,
        mappings: newMappings
      });
    } else {
      // Update mapping
      setMappingConfig({
        ...mappingConfig,
        mappings: {
          ...mappingConfig.mappings,
          [fieldName]: mapping
        }
      });
    }
    setEditingField(null);
  };

  const getModelIcon = (model: TargetModel) => {
    switch (model) {
      case 'interactions':
        return '🔗';
      case 'entities':
        return '🧬';
      case 'controlled_vocabularies':
        return '📚';
    }
  };

  const targetFields = getFieldsForModel(targetModel);
  const requiredFieldsMapped = targetFields
    .filter(f => f.required)
    .every(f => mappingConfig.mappings[f.name]);

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <div className="border-b">
        <div className="container mx-auto px-4 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <Button 
                variant="ghost" 
                size="sm" 
                onClick={() => router.back()}
                className="flex items-center gap-2"
              >
                <ArrowLeft className="w-4 h-4" />
                Back
              </Button>
              <div>
                <h1 className="text-2xl font-bold">Configure Column Mapping</h1>
                <p className="text-muted-foreground">
                  {datasource.name} / {dataset.name}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                onClick={() => setShowPreview(true)}
                disabled={Object.keys(mappingConfig.mappings).length === 0}
              >
                <Play className="w-4 h-4 mr-2" />
                Test Mapping
              </Button>
              <Button
                onClick={handleSave}
                disabled={isLoading || !requiredFieldsMapped}
              >
                <Save className="w-4 h-4 mr-2" />
                Save Configuration
              </Button>
            </div>
          </div>
        </div>
      </div>

      <div className="container mx-auto px-4 py-6 max-w-5xl">
        {/* Target Model Selection */}
        <Card className="mb-6">
          <CardHeader>
            <CardTitle>Select Target Model</CardTitle>
            <CardDescription>
              Choose which silver model this dataset should be mapped to
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {(['interactions', 'entities', 'controlled_vocabularies'] as TargetModel[]).map((model) => (
                <div
                  key={model}
                  className={`p-4 border rounded-lg cursor-pointer transition-all ${
                    targetModel === model 
                      ? 'border-primary bg-primary/5' 
                      : 'border-border hover:border-primary/50'
                  }`}
                  onClick={() => {
                    setTargetModel(model);
                    setMappingConfig({
                      targetModel: model,
                      mappings: {}
                    });
                  }}
                >
                  <div className="flex items-center gap-3">
                    <span className="text-2xl">{getModelIcon(model)}</span>
                    <div>
                      <h3 className="font-semibold capitalize">
                        {model.replace('_', ' ')}
                      </h3>
                      <p className="text-sm text-muted-foreground">
                        {model === 'interactions' && 'Protein-protein, drug-target interactions'}
                        {model === 'entities' && 'Proteins, drugs, complexes, phenotypes'}
                        {model === 'controlled_vocabularies' && 'Ontology terms and vocabularies'}
                      </p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Progress Indicator */}
        {targetFields.filter(f => f.required).length > 0 && (
          <div className="mb-6 p-4 bg-muted/50 rounded-lg">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium">Required Fields Progress</span>
              <span className="text-sm text-muted-foreground">
                {targetFields.filter(f => f.required && mappingConfig.mappings[f.name]).length} / {targetFields.filter(f => f.required).length}
              </span>
            </div>
            <div className="w-full bg-background rounded-full h-2">
              <div 
                className="bg-primary h-2 rounded-full transition-all"
                style={{
                  width: `${(targetFields.filter(f => f.required && mappingConfig.mappings[f.name]).length / targetFields.filter(f => f.required).length) * 100}%`
                }}
              />
            </div>
          </div>
        )}

        {/* Target Fields */}
        <div className="space-y-4">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-lg font-semibold">Map Fields</h2>
            <p className="text-sm text-muted-foreground">
              Click on a field to configure its mapping
            </p>
          </div>
          
          {targetFields.map((field) => (
            <TargetFieldCard
              key={field.name}
              field={field}
              mapping={mappingConfig.mappings[field.name]}
              sourceColumns={sourceColumns}
              isEditing={editingField === field.name}
              onEdit={() => setEditingField(field.name)}
              onUpdate={(mapping) => handleFieldUpdate(field.name, mapping)}
              onCancel={() => setEditingField(null)}
              datasourceId={datasource.id}
              datasetName={dataset.name}
              targetModel={targetModel}
            />
          ))}
        </div>

        {/* Preview Panel */}
        {showPreview && (
          <PreviewPanel
            mappingConfig={mappingConfig}
            datasourceId={datasource.id}
            datasetName={dataset.name}
            onClose={() => setShowPreview(false)}
          />
        )}
      </div>
    </div>
  );
}