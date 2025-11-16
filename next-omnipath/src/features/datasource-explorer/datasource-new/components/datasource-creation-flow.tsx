"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { Card } from "@/components/ui/card";
import { DatasourceBasicInfo } from "./steps/datasource-basic-info";
import { DatasourceDescription } from "./steps/datasource-description";
import { DatasetConfiguration } from "./steps/dataset-configuration";
import { DatasourceAccess } from "./steps/datasource-access";
import { DatasourceFormData } from "../types";
import { createDatasource } from "../api/mutations";
import { toast } from "sonner";
import confetti from "canvas-confetti";
import { Progress } from "@/components/ui/progress";
import { Button } from "@/components/ui/button";
import {
  ChevronLeft,
  ChevronRight,
  Loader2,
  Rocket,
} from "lucide-react";
import { useRouter } from "next/navigation";

export function DatasourceCreationFlow() {
  const [step, setStep] = useState(1);
  const [datasourceId, setDatasourceId] = useState<string>("");
  const [isCreating, setIsCreating] = useState(false);
  const [canProceed, setCanProceed] = useState(false);
  const [isDatasourceCreated, setIsDatasourceCreated] = useState(false);
  const router = useRouter();

  const form = useForm<DatasourceFormData>({
    defaultValues: {
      id: "",
      name: "",
      description: "",
      license: "",
      primaryPubmed: "",
      health: "success",
      website: "",
      updateCategory: undefined,
      accessCategory: undefined,
      datasets: [{
        name: "",
        entityType: "protein",
        category: "interaction",
        types: [],
        evidenceLevel: "literature_curated",
        taxonScope: "multi-species"
      }],
    },
    mode: "onChange",
  });

  const totalSteps = 4;

  const handleNext = () => {
    if (canProceed) {
      setStep(step + 1);
      setCanProceed(false);
    }
  };

  const handleBack = () => step > 1 && setStep(step - 1);

  const handleSubmit = async (data: DatasourceFormData) => {
    setIsCreating(true);
    try {
      const result = await createDatasource(data);
      
      if (result.success) {
        setDatasourceId(result.datasourceId || data.id);
        setIsDatasourceCreated(true);
        triggerConfetti();
        toast.success("Your datasource has been created successfully.");
        setStep(totalSteps + 1);
      } else {
        throw new Error("Failed to create datasource");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to create datasource. Please try again.");
      console.error(error);
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <Card className="w-full max-w-4xl relative md:h-[85vh] overflow-hidden dark:border-gray-700 pb-0">
        <div className="flex flex-col h-full">
          <div className="px-6 pt-4 pb-2 my-4">
            <Progress value={(step / totalSteps) * 100} className="h-2" />
            <div className="flex justify-between mt-2 text-sm text-gray-500 dark:text-gray-400">
              <span>Basic Info</span>
              <span>Description</span>
              <span>Datasets</span>
              <span>Review</span>
            </div>
          </div>

          <div className="px-6 py-8 flex-1 overflow-auto">
            {step === 1 && (
              <DatasourceBasicInfo form={form} setCanProceed={setCanProceed} />
            )}
            {step === 2 && (
              <DatasourceDescription form={form} setCanProceed={setCanProceed} />
            )}
            {step === 3 && (
              <DatasetConfiguration form={form} setCanProceed={setCanProceed} />
            )}
            {step === 4 && (
              <DatasourceAccess form={form} setCanProceed={setCanProceed} />
            )}
            {step === totalSteps + 1 && (
              <div className="flex flex-col items-center justify-center py-12 space-y-4">
                <h2 className="text-3xl font-semibold text-center">
                  Your datasource has been created successfully!
                </h2>
                <p className="text-gray-500">
                  The YAML configuration has been generated and saved.
                </p>
                <div className="flex space-x-4">
                  <Button
                    onClick={() => router.push(`/sources/${datasourceId}`)}
                    variant="default"
                  >
                    View Datasource
                  </Button>
                  <Button
                    onClick={() => router.push('/sources')}
                    variant="outline"
                  >
                    Back to Explorer
                  </Button>
                </div>
              </div>
            )}
          </div>

          <div className="px-6 py-4 border-t bg-gray-50 dark:bg-gray-800 dark:border-gray-700 mt-auto">
            <div className="flex justify-between items-center">
              <Button
                onClick={handleBack}
                variant="ghost"
                className="flex items-center space-x-2"
                disabled={step === 1 || step === totalSteps + 1}
              >
                <ChevronLeft className="w-4 h-4" />
                <span>Back</span>
              </Button>

              {step < totalSteps ? (
                <Button
                  onClick={handleNext}
                  className="flex items-center space-x-2"
                  disabled={!canProceed}
                >
                  <span>Continue</span>
                  <ChevronRight className="w-4 h-4" />
                </Button>
              ) : step === totalSteps ? (
                <Button
                  onClick={form.handleSubmit(handleSubmit)}
                  disabled={isCreating || !canProceed || isDatasourceCreated}
                  className="flex items-center space-x-2"
                >
                  {isCreating ? (
                    <Loader2 className="w-4 h-4 animate-spin mr-2" />
                  ) : (
                    <Rocket className="w-4 h-4 mr-2" />
                  )}
                  {isCreating ? "Creating..." : "Create Datasource"}
                </Button>
              ) : null}
            </div>
          </div>
        </div>
      </Card>
    </div>
  );
}

function triggerConfetti() {
  const duration = 5 * 1000;
  const animationEnd = Date.now() + duration;
  const defaults = { startVelocity: 30, spread: 360, ticks: 60, zIndex: 0 };

  const randomInRange = (min: number, max: number) =>
    Math.random() * (max - min) + min;

  const interval = setInterval(() => {
    const timeLeft = animationEnd - Date.now();

    if (timeLeft <= 0) {
      return clearInterval(interval);
    }

    const particleCount = 50 * (timeLeft / duration);
    confetti({
      ...defaults,
      particleCount,
      origin: { x: randomInRange(0.1, 0.3), y: Math.random() - 0.2 },
    });
    confetti({
      ...defaults,
      particleCount,
      origin: { x: randomInRange(0.7, 0.9), y: Math.random() - 0.2 },
    });
  }, 250);
}