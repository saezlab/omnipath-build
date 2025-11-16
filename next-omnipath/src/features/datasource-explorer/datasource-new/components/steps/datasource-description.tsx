import { useEffect } from "react";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Clock, Download } from "lucide-react";
import { DatasourceFormData } from "../../types";
import { UseFormReturn } from "react-hook-form";
import { UPDATE_CATEGORIES, ACCESS_CATEGORIES } from "../../../types/datasource";

interface DatasourceDescriptionProps {
  form: UseFormReturn<DatasourceFormData>;
  setCanProceed: (canProceed: boolean) => void;
}

export function DatasourceDescription({
  form,
  setCanProceed,
}: DatasourceDescriptionProps) {
  const description = form.watch("description");
  const updateCategory = form.watch("updateCategory");
  const accessCategory = form.watch("accessCategory");

  useEffect(() => {
    const isValid = 
      description?.trim().length >= 50 &&
      !!updateCategory &&
      !!accessCategory;
    setCanProceed(isValid);
  }, [description, updateCategory, accessCategory, setCanProceed]);

  return (
    <div className="space-y-6 p-6">
      <div className="space-y-4">
        <div className="flex items-center space-x-2 mb-4">
          <Label
            htmlFor="description"
            className="text-2xl font-bold dark:text-white"
          >
            Describe your datasource
          </Label>
          <div className="flex-1 h-px bg-gray-200 dark:bg-gray-700" />
        </div>

        <div className="bg-gray-50 dark:bg-gray-800 p-4 rounded-lg mb-4">
          <h3 className="font-medium mb-2 dark:text-white">
            Tips for a great description:
          </h3>
          <ul className="list-disc list-inside text-gray-600 dark:text-gray-400 space-y-1">
            <li>Explain what type of data is included</li>
            <li>Mention the primary source or methodology</li>
            <li>Include any unique features or coverage</li>
          </ul>
        </div>

        <Textarea
          {...form.register("description")}
          className="min-h-[150px] text-lg p-4 border-2 focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:border-gray-700 dark:text-white"
          placeholder="This datasource contains protein-protein interactions curated from..."
        />

        <div className="flex items-center justify-between text-sm">
          <span className="text-gray-500 dark:text-gray-400">
            {description?.length || 0} characters
          </span>
          <span
            className={`font-medium ${
              description?.length >= 50
                ? "text-green-500"
                : "text-gray-500 dark:text-gray-400"
            }`}
          >
            Minimum 50 characters required
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="space-y-4">
          <div className="flex items-center space-x-2">
            <Label className="text-lg font-semibold dark:text-white">
              Update Category
            </Label>
            <Clock className="w-4 h-4 text-gray-500" />
          </div>
          <div className="space-y-2">
            {UPDATE_CATEGORIES.map((category) => (
              <Button
                key={category.value}
                onClick={() => form.setValue("updateCategory", category.value as 'one_time_paper' | 'discontinued' | 'infrequent' | 'frequent')}
                variant={
                  form.watch("updateCategory") === category.value
                    ? "default"
                    : "outline"
                }
                className="w-full justify-start text-left h-auto p-3"
                type="button"
              >
                <div>
                  <div className="font-medium">{category.label}</div>
                  <div className="text-xs text-gray-500 dark:text-gray-400">
                    {category.description}
                  </div>
                </div>
              </Button>
            ))}
          </div>
        </div>

        <div className="space-y-4">
          <div className="flex items-center space-x-2">
            <Label className="text-lg font-semibold dark:text-white">
              Access Category
            </Label>
            <Download className="w-4 h-4 text-gray-500" />
          </div>
          <div className="space-y-2">
            {ACCESS_CATEGORIES.map((category) => (
              <Button
                key={category.value}
                onClick={() => form.setValue("accessCategory", category.value as 'file_download' | 'api' | 'web_scraping')}
                variant={
                  form.watch("accessCategory") === category.value
                    ? "default"
                    : "outline"
                }
                className="w-full justify-start text-left h-auto p-3"
                type="button"
              >
                <div>
                  <div className="font-medium">{category.label}</div>
                  <div className="text-xs text-gray-500 dark:text-gray-400">
                    {category.description}
                  </div>
                </div>
              </Button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}