"use client"

import { useEffect, useState } from "react"
import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table"
import { Loader2 } from "lucide-react"

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { getBronzeDataForSource, type TableDataResponse } from "../api/bronze-queries"

interface DataTableProps {
  sourceId: string
}

export function DataTable({ sourceId }: DataTableProps) {
  const [tableData, setTableData] = useState<TableDataResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true)
        setError(null)
        const data = await getBronzeDataForSource(sourceId, currentPage.toString(), pageSize)
        setTableData(data)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to fetch table data')
        console.error('Error fetching table data:', err)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
  }, [sourceId, currentPage, pageSize])

  // Create columns dynamically from the first row of data
  const columns: ColumnDef<Record<string, unknown>>[] = tableData?.data && tableData.data.length > 0 
    ? Object.keys(tableData.data[0]).map((key) => ({
        accessorKey: key,
        header: key,
        cell: ({ getValue }) => {
          const value = getValue()
          // Truncate long values for display
          if (typeof value === 'string' && value.length > 100) {
            return <span title={value}>{value.substring(0, 100)}...</span>
          }
          return value
        }
      }))
    : []

  const table = useReactTable({
    data: tableData?.data || [],
    columns,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
  })

  if (loading) {
    return (
      <div className="flex items-center justify-center py-10">
        <Loader2 className="h-6 w-6 animate-spin" />
        <span className="ml-2">Loading data...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="text-center py-10">
        <p className="text-red-500">Error: {error}</p>
        <p className="text-muted-foreground mt-2">Bronze layer data may not be available for this source.</p>
      </div>
    )
  }

  if (!tableData || tableData.data.length === 0) {
    return (
      <div className="text-center py-10">
        <p className="text-muted-foreground">No data available for this source.</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <p className="text-sm text-muted-foreground">
            Showing {((currentPage - 1) * pageSize) + 1} to {Math.min(currentPage * pageSize, tableData.pagination.total)} of {tableData.pagination.total.toLocaleString()} records
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select
            value={pageSize.toString()}
            onValueChange={(value) => {
              setPageSize(Number(value))
              setCurrentPage(1)
            }}
          >
            <SelectTrigger className="h-8 w-[70px]">
              <SelectValue placeholder={pageSize.toString()} />
            </SelectTrigger>
            <SelectContent side="top">
              {[10, 20, 30, 40, 50].map((size) => (
                <SelectItem key={size} value={size.toString()}>
                  {size}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id}>
                {headerGroup.headers.map((header) => {
                  return (
                    <TableHead key={header.id}>
                      {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
                    </TableHead>
                  )
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows?.length ? (
              table.getRowModel().rows.map((row) => (
                <TableRow key={row.id} data-state={row.getIsSelected() && "selected"}>
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</TableCell>
                  ))}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell colSpan={columns.length} className="h-24 text-center">
                  No results.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      <div className="flex items-center justify-end space-x-2">
        <span className="text-sm text-muted-foreground">
          Page {currentPage} of {tableData.pagination.pages}
        </span>
        <Button 
          variant="outline" 
          size="sm" 
          onClick={() => setCurrentPage(prev => Math.max(1, prev - 1))} 
          disabled={currentPage <= 1}
        >
          Previous
        </Button>
        <Button 
          variant="outline" 
          size="sm" 
          onClick={() => setCurrentPage(prev => prev + 1)} 
          disabled={currentPage >= tableData.pagination.pages}
        >
          Next
        </Button>
      </div>
    </div>
  )
}
