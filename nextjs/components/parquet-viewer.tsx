"use client"

import { useEffect, useState } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Spinner } from "@/components/ui/spinner"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { Database, FileText, AlertCircle } from "lucide-react"
import * as duckdb from "@duckdb/duckdb-wasm"

interface ParquetViewerProps {
  file: File
}

interface ColumnInfo {
  column_name: string
  column_type: string
  null: string
  key: string | null
  default: string | null
  extra: string | null
}

export function ParquetViewer({ file }: ParquetViewerProps) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string>("")
  const [data, setData] = useState<any[]>([])
  const [schema, setSchema] = useState<ColumnInfo[]>([])
  const [rowCount, setRowCount] = useState<number>(0)

  useEffect(() => {
    let mounted = true

    async function loadParquet() {
      try {
        setLoading(true)
        setError("")

        console.log("[v0] Initializing DuckDB...")

        // Initialize DuckDB
        const JSDELIVR_BUNDLES = duckdb.getJsDelivrBundles()
        const bundle = await duckdb.selectBundle(JSDELIVR_BUNDLES)
        const worker_url = URL.createObjectURL(
          new Blob([`importScripts("${bundle.mainWorker}");`], { type: "text/javascript" }),
        )
        const worker = new Worker(worker_url)
        const logger = new duckdb.ConsoleLogger()
        const db = new duckdb.AsyncDuckDB(logger, worker)
        await db.instantiate(bundle.mainModule, bundle.pthreadWorker)
        URL.revokeObjectURL(worker_url)

        console.log("[v0] DuckDB initialized, loading file...")

        // Register the file
        const conn = await db.connect()
        const arrayBuffer = await file.arrayBuffer()
        await db.registerFileBuffer(file.name, new Uint8Array(arrayBuffer))

        console.log("[v0] File registered, querying data...")

        // Get row count
        const countResult = await conn.query(`SELECT COUNT(*) as count FROM '${file.name}'`)
        const count = countResult.toArray()[0].count

        // Get schema information
        const schemaResult = await conn.query(`DESCRIBE SELECT * FROM '${file.name}'`)
        const schemaData = schemaResult.toArray()

        // Get sample data (first 100 rows)
        const dataResult = await conn.query(`SELECT * FROM '${file.name}' LIMIT 100`)
        const rows = dataResult.toArray()

        console.log("[v0] Data loaded successfully:", { rowCount: count, columns: schemaData.length })

        if (mounted) {
          setRowCount(Number(count))
          setSchema(schemaData as ColumnInfo[])
          setData(rows)
        }

        await conn.close()
        await db.terminate()
      } catch (err) {
        console.error("[v0] Error loading parquet file:", err)
        if (mounted) {
          setError(err instanceof Error ? err.message : "Failed to load parquet file")
        }
      } finally {
        if (mounted) {
          setLoading(false)
        }
      }
    }

    loadParquet()

    return () => {
      mounted = false
    }
  }, [file])

  if (loading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-12">
          <div className="flex flex-col items-center gap-4">
            <Spinner className="h-8 w-8" />
            <p className="text-sm text-muted-foreground">Loading parquet file...</p>
          </div>
        </CardContent>
      </Card>
    )
  }

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertCircle className="h-4 w-4" />
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    )
  }

  return (
    <Tabs defaultValue="data" className="space-y-4">
      <TabsList>
        <TabsTrigger value="data" className="flex items-center gap-2">
          <FileText className="h-4 w-4" />
          Data
        </TabsTrigger>
        <TabsTrigger value="schema" className="flex items-center gap-2">
          <Database className="h-4 w-4" />
          Schema
        </TabsTrigger>
      </TabsList>

      <TabsContent value="data" className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle>Data Preview</CardTitle>
            <CardDescription>Showing first 100 rows of {rowCount.toLocaleString()} total rows</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-auto rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    {schema.map((col) => (
                      <TableHead key={col.column_name} className="whitespace-nowrap">
                        {col.column_name}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.map((row, idx) => (
                    <TableRow key={idx}>
                      {schema.map((col) => (
                        <TableCell key={col.column_name} className="whitespace-nowrap">
                          {row[col.column_name] === null ? (
                            <span className="text-muted-foreground italic">null</span>
                          ) : (
                            String(row[col.column_name])
                          )}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      </TabsContent>

      <TabsContent value="schema" className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle>Schema Information</CardTitle>
            <CardDescription>
              {schema.length} columns • {rowCount.toLocaleString()} rows
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-auto rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Column Name</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Nullable</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {schema.map((col) => (
                    <TableRow key={col.column_name}>
                      <TableCell className="font-mono">{col.column_name}</TableCell>
                      <TableCell>
                        <Badge variant="secondary">{col.column_type}</Badge>
                      </TableCell>
                      <TableCell>{col.null === "YES" ? "Yes" : "No"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      </TabsContent>
    </Tabs>
  )
}
