import { useEffect, useState } from "react";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { 
  FileText, 
  Globe, 
  Database, 
  Clock, 
  Download, 
  HeartHandshake,
  Code
} from "lucide-react";
import { DatasourceFormData } from "../../types";
import { UseFormReturn } from "react-hook-form";
import { UPDATE_CATEGORIES, ACCESS_CATEGORIES, HEALTH_STATUSES, ENTITY_TYPES, CATEGORIES, EVIDENCE_LEVELS, TAXON_SCOPES } from "../../../types/datasource";
import * as yaml from 'js-yaml';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

interface DatasourceAccessProps {
  form: UseFormReturn<DatasourceFormData>;
  setCanProceed: (canProceed: boolean) => void;
}

export function DatasourceAccess({
  form,
  setCanProceed,
}: DatasourceAccessProps) {
  const [showYamlPreview, setShowYamlPreview] = useState(false);
  const formData = form.watch();

  useEffect(() => {
    // Always allow proceeding from review step if all previous steps were valid
    setCanProceed(true);
  }, [setCanProceed]);

  const generateYamlPreview = () => {
    const yamlData = {
      id: formData.id,
      name: formData.name,
      description: formData.description,
      license: formData.license,
      primary_pubmed: formData.primaryPubmed || null,
      health: formData.health,
      website: formData.website,
      update_category: formData.updateCategory,
      access_category: formData.accessCategory,
      datasets: formData.datasets.map(dataset => ({
        name: dataset.name,
        entity_type: dataset.entityType,
        category: dataset.category,
        types: dataset.types,
        evidence_level: dataset.evidenceLevel,
        taxon_scope: dataset.taxonScope.replace('-', '_'),
        ...(dataset.download?.url && {
          download: {
            url: dataset.download.url,
            method: dataset.download.method,
            ...(dataset.download.delimiter && { delimiter: dataset.download.delimiter }),
            ...(dataset.download.header !== undefined && { header: dataset.download.header }),
            tags: []
          }
        }),
        data_processing: dataset.dataProcessing || {}
      }))
    };

    return yaml.dump(yamlData, {
      indent: 2,
      lineWidth: -1,
      noRefs: true,
      sortKeys: false
    });
  };

  const getUpdateCategoryLabel = (value: string) => 
    UPDATE_CATEGORIES.find(cat => cat.value === value)?.label || value;
  
  const getAccessCategoryLabel = (value: string) => 
    ACCESS_CATEGORIES.find(cat => cat.value === value)?.label || value;
    
  const getHealthStatusLabel = (value: string) => 
    HEALTH_STATUSES.find(status => status.value === value)?.label || value;

  const getEntityTypeLabel = (value: string) =>
    ENTITY_TYPES.find(type => type.value === value)?.label || value;

  const getCategoryLabel = (value: string) =>
    CATEGORIES.find(cat => cat.value === value)?.label || value;

  const getEvidenceLevelLabel = (value: string) =>
    EVIDENCE_LEVELS.find(level => level.value === value)?.label || value;

  const getTaxonScopeLabel = (value: string) =>
    TAXON_SCOPES.find(scope => scope.value === value)?.label || value;

  return (
    <div className="space-y-6 p-6">
      <div>
        <h2 className="text-2xl font-bold dark:text-white mb-2">Review & Submit</h2>
        <p className="text-gray-500 dark:text-gray-400">
          Review your datasource configuration before submitting
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center space-x-2">
            <Database className="w-5 h-5" />
            <span>Basic Information</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label className="text-sm text-gray-500">Name</Label>
              <p className="font-medium">{formData.name}</p>
            </div>
            <div>
              <Label className="text-sm text-gray-500">ID</Label>
              <p className="font-mono text-sm">{formData.id}</p>
            </div>
            <div>
              <Label className="text-sm text-gray-500">License</Label>
              <p className="font-medium">{formData.license}</p>
            </div>
            <div>
              <Label className="text-sm text-gray-500">PubMed ID</Label>
              <p className="font-medium">{formData.primaryPubmed || 'Not provided'}</p>
            </div>
            <div className="md:col-span-2">
              <Label className="text-sm text-gray-500">Website</Label>
              <a href={formData.website} target="_blank" rel="noopener noreferrer" 
                 className="text-blue-600 hover:underline flex items-center space-x-1">
                <Globe className="w-3 h-3" />
                <span>{formData.website}</span>
              </a>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center space-x-2">
            <FileText className="w-5 h-5" />
            <span>Description & Metadata</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label className="text-sm text-gray-500">Description</Label>
            <p className="text-sm mt-1">{formData.description}</p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <Label className="text-sm text-gray-500">Update Category</Label>
              <Badge variant="outline" className="mt-1">
                <Clock className="w-3 h-3 mr-1" />
                {getUpdateCategoryLabel(formData.updateCategory)}
              </Badge>
            </div>
            <div>
              <Label className="text-sm text-gray-500">Access Category</Label>
              <Badge variant="outline" className="mt-1">
                <Download className="w-3 h-3 mr-1" />
                {getAccessCategoryLabel(formData.accessCategory)}
              </Badge>
            </div>
            <div>
              <Label className="text-sm text-gray-500">Health Status</Label>
              <Badge 
                variant={formData.health === 'success' ? 'default' : 'destructive'} 
                className="mt-1"
              >
                <HeartHandshake className="w-3 h-3 mr-1" />
                {getHealthStatusLabel(formData.health)}
              </Badge>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span className="flex items-center space-x-2">
              <Database className="w-5 h-5" />
              <span>Datasets ({formData.datasets.length})</span>
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {formData.datasets.map((dataset, index) => (
              <div key={index} className="border rounded-lg p-4 space-y-3">
                <h4 className="font-medium">{dataset.name}</h4>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
                  <div>
                    <Label className="text-xs text-gray-500">Entity Type</Label>
                    <p>{getEntityTypeLabel(dataset.entityType)}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-gray-500">Category</Label>
                    <p>{getCategoryLabel(dataset.category)}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-gray-500">Evidence Level</Label>
                    <p>{getEvidenceLevelLabel(dataset.evidenceLevel)}</p>
                  </div>
                  <div>
                    <Label className="text-xs text-gray-500">Taxon Scope</Label>
                    <p>{getTaxonScopeLabel(dataset.taxonScope)}</p>
                  </div>
                  <div className="md:col-span-2">
                    <Label className="text-xs text-gray-500">Types</Label>
                    <div className="flex flex-wrap gap-1 mt-1">
                      {dataset.types.map((type, i) => (
                        <Badge key={i} variant="secondary" className="text-xs">
                          {type.replace(/_/g, ' ')}
                        </Badge>
                      ))}
                    </div>
                  </div>
                </div>
                {dataset.download?.url && (
                  <div className="pt-2 border-t">
                    <Label className="text-xs text-gray-500">Download URL</Label>
                    <p className="text-sm font-mono break-all">{dataset.download.url}</p>
                  </div>
                )}
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <div className="flex justify-center pt-4">
        <Button
          type="button"
          onClick={() => setShowYamlPreview(true)}
          variant="outline"
          className="flex items-center space-x-2"
        >
          <Code className="w-4 h-4" />
          <span>Preview YAML Configuration</span>
        </Button>
      </div>

      <Dialog open={showYamlPreview} onOpenChange={setShowYamlPreview}>
        <DialogContent className="max-w-4xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>YAML Configuration Preview</DialogTitle>
          </DialogHeader>
          <div className="mt-4">
            <pre className="bg-gray-100 dark:bg-gray-800 p-4 rounded-lg overflow-x-auto">
              <code className="text-sm">{generateYamlPreview()}</code>
            </pre>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}