"use client";

import { useRouter } from "next/navigation";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DataTable } from "./data-table";
import { Badge } from "@/components/ui/badge";
import type { DataSource } from "../api/queries";

interface DataSourceDetailsProps {
  source: DataSource;
  sourceId: string;
}

export function DataSourceDetails({ source, sourceId }: DataSourceDetailsProps) {
  const router = useRouter();

  return (
    <div className="container py-10">
      <Button variant="outline" className="mb-6" onClick={() => router.back()}>
        <ArrowLeft className="mr-2 h-4 w-4" /> Back to sources
      </Button>

      <div className="flex flex-col gap-6">
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-2xl">{source.name}</CardTitle>
                <CardDescription className="text-lg mt-1">{source.description}</CardDescription>
              </div>
              <Badge variant={source.category === "interactions" ? "default" : "outline"}>{source.category}</Badge>
            </div>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              <div className="flex flex-col gap-2">
                <div className="text-sm font-medium">License</div>
                <div className="text-sm text-muted-foreground">{source.license}</div>
              </div>
              <div className="flex flex-col gap-2">
                <div className="text-sm font-medium">Citation</div>
                <div className="text-sm text-muted-foreground">{source.citation}</div>
              </div>
              <div className="flex flex-col gap-2">
                <div className="text-sm font-medium">Website</div>
                <a
                  href={source.website}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm text-primary flex items-center gap-1 hover:underline"
                >
                  Visit website <ExternalLink className="h-3 w-3" />
                </a>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Bronze Layer Data</CardTitle>
            <CardDescription>Raw source data from {source.name} before transformations.</CardDescription>
          </CardHeader>
          <CardContent>
            <DataTable sourceId={sourceId} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}