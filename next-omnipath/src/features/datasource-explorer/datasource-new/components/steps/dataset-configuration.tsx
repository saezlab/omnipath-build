import { useEffect, useState, useMemo } from "react";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Plus, Trash2, Database, ChevronDown, ChevronUp, Upload } from "lucide-react";
import { DatasourceFormData, DatasetFormData, INTERACTION_TYPES, ANNOTATION_TYPES, ONTOLOGY_TYPES } from "../../types";
import { UseFormReturn } from "react-hook-form";
import { ENTITY_TYPES, CATEGORIES, EVIDENCE_LEVELS, TAXON_SCOPES } from "../../../types/datasource";
import { Collapsible, CollapsibleContent } from "@/components/ui/collapsible";

interface DatasetConfigurationProps {
  form: UseFormReturn<DatasourceFormData>;
  setCanProceed: (canProceed: boolean) => void;
}

export function DatasetConfiguration({
  form,
  setCanProceed,
}: DatasetConfigurationProps) {
  const datasets = useMemo(() => form.watch("datasets") || [], [form]);
  const [expandedDatasets, setExpandedDatasets] = useState<number[]>([0]);

  useEffect(() => {
    const isValid = datasets.length >= 1 && datasets.length <= 5 &&
      datasets.every(dataset => 
        dataset.name?.trim().length >= 3 &&
        dataset.entityType &&
        dataset.category &&
        (dataset.category === 'ontology' || dataset.types?.length > 0) &&
        dataset.evidenceLevel &&
        dataset.taxonScope
      );
    setCanProceed(isValid);
  }, [datasets, setCanProceed]);

  const addDataset = () => {
    if (datasets.length >= 5) return;
    
    const newDataset: DatasetFormData = {
      name: "",
      entityType: "protein",
      category: "interaction",
      types: [],
      evidenceLevel: "literature_curated",
      taxonScope: "multi-species"
    };
    
    form.setValue("datasets", [...datasets, newDataset]);
    setExpandedDatasets([...expandedDatasets, datasets.length]);
  };

  const removeDataset = (index: number) => {
    form.setValue("datasets", datasets.filter((_, i) => i !== index));
    setExpandedDatasets(expandedDatasets.filter(i => i !== index).map(i => i > index ? i - 1 : i));
  };

  const updateDataset = (index: number, field: keyof DatasetFormData, value: unknown) => {
    const updatedDatasets = [...datasets];
    updatedDatasets[index] = { ...updatedDatasets[index], [field]: value };
    form.setValue("datasets", updatedDatasets);
  };

  const updateDownloadConfig = (index: number, field: string, value: unknown) => {
    const updatedDatasets = [...datasets];
    const currentDownload = updatedDatasets[index].download;
    
    updatedDatasets[index] = {
      ...updatedDatasets[index],
      download: {
        url: currentDownload?.url || "",
        method: currentDownload?.method || "GET",
        delimiter: currentDownload?.delimiter,
        header: currentDownload?.header,
        extractFromZip: currentDownload?.extractFromZip,
        postData: currentDownload?.postData,
        [field]: value
      }
    };
    form.setValue("datasets", updatedDatasets);
  };

  const getTypeOptions = (category: string) => {
    switch (category) {
      case 'interaction':
        return INTERACTION_TYPES;
      case 'annotation':
        return ANNOTATION_TYPES;
      case 'ontology':
        return ONTOLOGY_TYPES;
      default:
        return [];
    }
  };

  const toggleExpanded = (index: number) => {
    setExpandedDatasets(prev =>
      prev.includes(index)
        ? prev.filter(i => i !== index)
        : [...prev, index]
    );
  };

  return (
    <div className="space-y-6 p-6">
      <div>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-2xl font-bold dark:text-white">Dataset Configuration</h2>
            <p className="text-gray-500 dark:text-gray-400 mt-1">
              Add between 1 and 5 datasets for this datasource
            </p>
          </div>
          <Button
            type="button"
            onClick={addDataset}
            disabled={datasets.length >= 5}
            variant="outline"
            className="flex items-center space-x-2"
          >
            <Plus className="w-4 h-4" />
            <span>Add Dataset</span>
          </Button>
        </div>

        <div className="space-y-4">
          {datasets.map((dataset, index) => (
            <Card key={index} className="relative">
              <CardHeader className="cursor-pointer" onClick={() => toggleExpanded(index)}>
                <div className="flex items-center justify-between">
                  <CardTitle className="text-lg flex items-center space-x-2">
                    <Database className="w-5 h-5" />
                    <span>
                      {dataset.name || `Dataset ${index + 1}`}
                    </span>
                  </CardTitle>
                  <div className="flex items-center space-x-2">
                    {expandedDatasets.includes(index) ? (
                      <ChevronUp className="w-5 h-5" />
                    ) : (
                      <ChevronDown className="w-5 h-5" />
                    )}
                  </div>
                </div>
              </CardHeader>
              
              <Collapsible open={expandedDatasets.includes(index)}>
                <CollapsibleContent>
                  <CardContent className="space-y-4">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div className="space-y-2">
                        <Label>Dataset Name</Label>
                        <Input
                          value={dataset.name}
                          onChange={(e) => updateDataset(index, "name", e.target.value)}
                          placeholder="e.g., protein_interactions"
                          pattern="^[a-z0-9_]+$"
                        />
                        <p className="text-xs text-gray-500">Lowercase alphanumeric with underscores</p>
                      </div>

                      <div className="space-y-2">
                        <Label>Entity Type</Label>
                        <Select
                          value={dataset.entityType}
                          onValueChange={(value) => updateDataset(index, "entityType", value)}
                        >
                          <SelectTrigger>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {ENTITY_TYPES.map((type) => (
                              <SelectItem key={type.value} value={type.value}>
                                <span className="flex items-center space-x-2">
                                  <span>{type.icon}</span>
                                  <span>{type.label}</span>
                                </span>
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>

                      <div className="space-y-2">
                        <Label>Category</Label>
                        <Select
                          value={dataset.category}
                          onValueChange={(value) => {
                            const updatedDatasets = [...datasets];
                            updatedDatasets[index] = {
                              ...updatedDatasets[index],
                              category: value as DatasetFormData['category'],
                              types: [] // Reset types when category changes
                            };
                            form.setValue("datasets", updatedDatasets);
                          }}
                        >
                          <SelectTrigger>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {CATEGORIES.map((cat) => (
                              <SelectItem key={cat.value} value={cat.value}>
                                {cat.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>

                      <div className="space-y-2">
                        <Label>Evidence Level</Label>
                        <Select
                          value={dataset.evidenceLevel}
                          onValueChange={(value) => updateDataset(index, "evidenceLevel", value)}
                        >
                          <SelectTrigger>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {EVIDENCE_LEVELS.map((level) => (
                              <SelectItem key={level.value} value={level.value}>
                                {level.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>

                      <div className="space-y-2">
                        <Label>Taxon Scope</Label>
                        <Select
                          value={dataset.taxonScope}
                          onValueChange={(value) => updateDataset(index, "taxonScope", value)}
                        >
                          <SelectTrigger>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {TAXON_SCOPES.map((scope) => (
                              <SelectItem key={scope.value} value={scope.value}>
                                {scope.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                    </div>

                    {dataset.category !== 'ontology' && (
                      <div className="space-y-2">
                        <Label>Types ({dataset.category})</Label>
                        <div className="border rounded-lg p-3 space-y-2 max-h-40 overflow-y-auto">
                          {getTypeOptions(dataset.category).length === 0 ? (
                            <p className="text-sm text-gray-500">No types available for {dataset.category}</p>
                          ) : (
                            getTypeOptions(dataset.category).map((type) => (
                            <div key={type} className="flex items-center space-x-2">
                              <Checkbox
                                checked={dataset.types?.includes(type) || false}
                                onCheckedChange={(checked) => {
                                  const currentTypes = dataset.types || [];
                                  if (checked) {
                                    updateDataset(index, "types", [...currentTypes, type]);
                                  } else {
                                    updateDataset(index, "types", currentTypes.filter(t => t !== type));
                                  }
                                }}
                              />
                              <Label className="text-sm font-normal cursor-pointer">
                                {type.replace(/_/g, ' ')}
                              </Label>
                            </div>
                          ))
                          )}
                        </div>
                      </div>
                    )}

                    <div className="space-y-4 pt-4 border-t">
                      <h4 className="font-medium">Download Configuration (Optional)</h4>
                      
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2 md:col-span-2">
                          <Label>Download URL</Label>
                          <Input
                            value={dataset.download?.url || ""}
                            onChange={(e) => updateDownloadConfig(index, "url", e.target.value)}
                            type="url"
                            placeholder="https://example.com/data.csv"
                          />
                        </div>

                        <div className="space-y-2">
                          <Label>HTTP Method</Label>
                          <Select
                            value={dataset.download?.method || "GET"}
                            onValueChange={(value) => updateDownloadConfig(index, "method", value)}
                          >
                            <SelectTrigger>
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="GET">GET</SelectItem>
                              <SelectItem value="POST">POST</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>

                        <div className="space-y-2">
                          <Label>Delimiter</Label>
                          <Input
                            value={dataset.download?.delimiter || ""}
                            onChange={(e) => updateDownloadConfig(index, "delimiter", e.target.value)}
                            placeholder="e.g., \t or ,"
                          />
                        </div>

                        <div className="flex items-center space-x-2">
                          <Checkbox
                            checked={dataset.download?.header || false}
                            onCheckedChange={(checked) => updateDownloadConfig(index, "header", checked)}
                          />
                          <Label className="text-sm font-normal">File has header row</Label>
                        </div>
                      </div>
                    </div>

                    <div className="space-y-4 pt-4 border-t">
                      <h4 className="font-medium">Or Upload Dataset File</h4>
                      
                      <div className="space-y-2">
                        <Label htmlFor={`file-upload-${index}`}>Upload CSV/TSV File</Label>
                        <div className="flex items-center space-x-4">
                          <Input
                            id={`file-upload-${index}`}
                            type="file"
                            accept=".csv,.tsv,.txt"
                            onChange={(e) => {
                              const file = e.target.files?.[0];
                              if (file) {
                                // TODO: Handle file upload
                                console.log('File selected:', file.name);
                              }
                            }}
                            className="flex-1"
                          />
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            className="flex items-center space-x-2"
                          >
                            <Upload className="w-4 h-4" />
                            <span>Upload</span>
                          </Button>
                        </div>
                        <p className="text-xs text-gray-500">
                          Upload a CSV or TSV file to automatically configure the dataset
                        </p>
                      </div>
                    </div>

                    {datasets.length > 1 && (
                      <div className="flex justify-end pt-4">
                        <Button
                          type="button"
                          onClick={() => removeDataset(index)}
                          variant="destructive"
                          size="sm"
                          className="flex items-center space-x-2"
                        >
                          <Trash2 className="w-4 h-4" />
                          <span>Remove Dataset</span>
                        </Button>
                      </div>
                    )}
                  </CardContent>
                </CollapsibleContent>
              </Collapsible>
            </Card>
          ))}
        </div>

        <div className="text-sm text-gray-500 dark:text-gray-400 mt-4">
          {datasets.length} of 5 datasets configured
        </div>
      </div>
    </div>
  );
}