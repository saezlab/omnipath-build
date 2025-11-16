import { useEffect, useState } from "react";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Database, Globe, FileText, RefreshCw } from "lucide-react";
import { DatasourceFormData, COMMON_LICENSES } from "../../types";
import { UseFormReturn } from "react-hook-form";
import { suggestDatasourceId, validateDatasourceId } from "../../api/mutations";
import { toast } from "sonner";

interface DatasourceBasicInfoProps {
  form: UseFormReturn<DatasourceFormData>;
  setCanProceed: (canProceed: boolean) => void;
}

export function DatasourceBasicInfo({
  form,
  setCanProceed,
}: DatasourceBasicInfoProps) {
  const [, setIsCheckingId] = useState(false);
  const [idAvailable, setIdAvailable] = useState<boolean | null>(null);

  const nameValue = form.watch("name");
  const idValue = form.watch("id");
  const licenseValue = form.watch("license");
  const websiteValue = form.watch("website");

  useEffect(() => {
    const isValid = 
      nameValue?.trim().length >= 3 && 
      idValue?.trim().length >= 3 &&
      licenseValue?.trim().length > 0 &&
      websiteValue?.trim().length > 0 &&
      idAvailable === true;
    setCanProceed(isValid);
  }, [nameValue, idValue, licenseValue, websiteValue, idAvailable, setCanProceed]);

  const handleIdCheck = async () => {
    if (!idValue || idValue.trim().length < 3) return;
    
    setIsCheckingId(true);
    try {
      const available = await validateDatasourceId(idValue);
      setIdAvailable(available);
      if (!available) {
        toast.error("This ID is already taken. Please choose another.");
      }
    } catch {
      toast.error("Failed to check ID availability");
    } finally {
      setIsCheckingId(false);
    }
  };

  const handleGenerateId = async () => {
    if (!nameValue || nameValue.trim().length < 3) {
      toast.error("Please enter a name first");
      return;
    }

    try {
      const suggestedId = await suggestDatasourceId(nameValue);
      form.setValue("id", suggestedId);
      setIdAvailable(true);
      toast.success("ID generated successfully");
    } catch {
      toast.error("Failed to generate ID");
    }
  };

  return (
    <div className="space-y-6 p-6">
      <div className="space-y-2">
        <div className="flex items-center space-x-2">
          <Label htmlFor="name" className="text-2xl font-bold dark:text-white">
            Name your datasource
          </Label>
          <div className="flex-1 h-px bg-gray-200 dark:bg-gray-700" />
        </div>
        <p className="text-gray-500 dark:text-gray-400">
          Choose a clear and descriptive name
        </p>
        <Input
          {...form.register("name")}
          className="mt-2 text-lg p-4 border-2 focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
          placeholder="e.g., BioGRID Protein Interactions"
        />
      </div>

      <div className="space-y-2">
        <div className="flex items-center space-x-2">
          <Label htmlFor="id" className="text-xl font-bold dark:text-white">
            Datasource ID
          </Label>
          <Database className="w-5 h-5 text-gray-500" />
        </div>
        <p className="text-gray-500 dark:text-gray-400 text-sm">
          A unique identifier (lowercase, alphanumeric with underscores)
        </p>
        <div className="flex space-x-2">
          <Input
            {...form.register("id")}
            className="flex-1 p-3 border-2 focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
            placeholder="e.g., biogrid_interactions"
            pattern="^[a-z0-9_]+$"
            onBlur={handleIdCheck}
          />
          <Button
            type="button"
            onClick={handleGenerateId}
            variant="outline"
            className="px-4"
          >
            <RefreshCw className="w-4 h-4 mr-2" />
            Generate
          </Button>
        </div>
        {idAvailable !== null && (
          <p className={`text-sm ${idAvailable ? 'text-green-600' : 'text-red-600'}`}>
            {idAvailable ? '✓ ID is available' : '✗ ID is already taken'}
          </p>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="space-y-2">
          <div className="flex items-center space-x-2">
            <Label htmlFor="license" className="text-lg font-semibold dark:text-white">
              License
            </Label>
            <FileText className="w-4 h-4 text-gray-500" />
          </div>
          <Select
            value={form.watch("license")}
            onValueChange={(value) => form.setValue("license", value)}
          >
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Select a license" />
            </SelectTrigger>
            <SelectContent>
              {COMMON_LICENSES.map((license) => (
                <SelectItem key={license.value} value={license.value}>
                  {license.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <div className="flex items-center space-x-2">
            <Label htmlFor="primaryPubmed" className="text-lg font-semibold dark:text-white">
              Primary PubMed ID
            </Label>
            <span className="text-sm text-gray-500">(optional)</span>
          </div>
          <Input
            {...form.register("primaryPubmed")}
            className="p-3 border-2 focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
            placeholder="e.g., 25428363"
            pattern="^\d*$"
          />
        </div>
      </div>

      <div className="space-y-2">
        <div className="flex items-center space-x-2">
          <Label htmlFor="website" className="text-lg font-semibold dark:text-white">
            Website
          </Label>
          <Globe className="w-4 h-4 text-gray-500" />
        </div>
        <Input
          {...form.register("website")}
          type="url"
          className="p-3 border-2 focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
          placeholder="https://example.com"
        />
      </div>
    </div>
  );
}