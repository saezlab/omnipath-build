"use client"

import * as React from "react"
import { ChevronRight, File, Folder, Database, FileText } from "lucide-react"
import { useRouter } from "next/navigation"
import { useDatabase } from "@/hooks/use-database-context"
import LayerBadge from "../app/components/LayerBadge"

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
} from "@/components/ui/sidebar"

// Helper function to build nested folder structure from file paths
function buildFolderStructure(files: any[], databaseName: string, layer: string) {
  const structure: { [key: string]: any } = {}
  
  files.forEach(file => {
    // Get the original file path but ensure it starts with database/layer
    let fullPath = file.path.replace(/^\//, '') // Remove leading slash
    
    // Extract relative path from the full path for folder structure
    let relativePath = file.path.replace(`/${databaseName}/${layer}/`, '').replace(/^\//, '')
    
    // Remove /data/ from the path structure for display
    const cleanRelativePath = relativePath.replace(/\/data\//g, '/').replace(/^data\//, '').replace(/\/data$/, '')
    
    // If no nested path, it's a direct file
    if (!cleanRelativePath.includes('/')) {
      structure[file.name] = {
        name: file.name,
        path: fullPath, // Use the original full path
        isFile: true
      }
      return
    }
    
    // Build nested folder structure
    const pathParts = cleanRelativePath.split('/').filter((part: string) => part !== 'data' && part !== '')
    const fileName = pathParts.pop()
    
    let current = structure
    
    // Create nested folders
    for (const part of pathParts) {
      if (!current[part]) {
        current[part] = {
          name: part,
          children: {},
          isFile: false
        }
      }
      current = current[part].children
    }
    
    // Add the file
    if (fileName) {
      current[fileName] = {
        name: fileName,
        path: fullPath, // Use the original full path
        isFile: true
      }
    }
  })
  
  // Convert to nested array format
  function convertToArray(obj: any): any[] {
    return Object.entries(obj).map(([, value]: [string, any]) => {
      if (value.isFile) {
        return {
          name: value.name,
          path: value.path
        }
      } else {
        return [value.name, ...convertToArray(value.children)]
      }
    })
  }
  
  return convertToArray(structure)
}

// Helper function to convert database files to nested array structure
function buildDatabaseTree(selectedDatabase: any) {
  if (!selectedDatabase) return []
  
  const tree: any[] = []
  
  // Add layers that have files
  if (selectedDatabase.layers.bronze.length > 0) {
    const bronzeStructure = buildFolderStructure(selectedDatabase.layers.bronze, selectedDatabase.name, 'bronze')
    tree.push([
      "Bronze Layer",
      ...bronzeStructure,
      { layer: 'bronze' as const }
    ])
  }
  
  if (selectedDatabase.layers.silver.length > 0) {
    const silverStructure = buildFolderStructure(selectedDatabase.layers.silver, selectedDatabase.name, 'silver')
    tree.push([
      "Silver Layer", 
      ...silverStructure,
      { layer: 'silver' as const }
    ])
  }
  
  if (selectedDatabase.layers.gold.length > 0) {
    const goldStructure = buildFolderStructure(selectedDatabase.layers.gold, selectedDatabase.name, 'gold')
    tree.push([
      "Gold Layer",
      ...goldStructure,
      { layer: 'gold' as const }
    ])
  }
  
  return tree
}

export function NavDatabaseTree() {
  const { selectedDatabase, loading } = useDatabase()
  const router = useRouter()
  
  if (loading) {
    return (
      <SidebarGroup>
        <SidebarGroupLabel>Database Structure</SidebarGroupLabel>
        <SidebarGroupContent>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton disabled>
                <FileText className="animate-pulse" />
                Loading...
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroupContent>
      </SidebarGroup>
    )
  }

  if (!selectedDatabase) {
    return (
      <SidebarGroup>
        <SidebarGroupLabel>Database Structure</SidebarGroupLabel>
        <SidebarGroupContent>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton disabled>
                <Database className="opacity-50" />
                <span className="text-muted-foreground">No database selected</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroupContent>
      </SidebarGroup>
    )
  }

  const handleFileSelect = (filePath: string) => {
    // Use Next.js router for client-side navigation (no page reload)
    router.push(`/viewer/${filePath}`)
  }

  const databaseTree = buildDatabaseTree(selectedDatabase)

  return (
    <SidebarGroup>
      <SidebarGroupLabel>Database Structure</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {databaseTree.map((item, index) => (
            <Tree key={index} item={item} onFileSelect={handleFileSelect} />
          ))}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  )
}

function Tree({ 
  item, 
  onFileSelect 
}: { 
  item: string | any[] | { name: string; path: string }
  onFileSelect?: (filePath: string) => void 
}) {
  // Handle file objects with name and path
  if (typeof item === 'object' && !Array.isArray(item) && 'name' in item && 'path' in item) {
    return (
      <SidebarMenuButton
        className="data-[active=true]:bg-transparent text-blue-600 dark:text-blue-400"
        onClick={() => onFileSelect?.(item.path)}
      >
        <File />
        {item.name}
      </SidebarMenuButton>
    )
  }

  if (!Array.isArray(item)) {
    // This is a simple string file (shouldn't happen with new structure)
    return (
      <SidebarMenuButton
        className="data-[active=true]:bg-transparent text-blue-600 dark:text-blue-400"
        onClick={() => onFileSelect?.(item)}
      >
        <File />
        {item}
      </SidebarMenuButton>
    )
  }

  // This is a folder (layer)
  const [name, ...items] = item
  const layerInfo = items.find(i => typeof i === 'object' && i.layer)
  const files = items.filter(i => typeof i !== 'object' || !i.layer)

  if (!files.length) {
    return null
  }

  return (
    <SidebarMenuItem>
      <Collapsible
        className="group/collapsible [&[data-state=open]>button>svg:first-child]:rotate-90"
        defaultOpen={true}
      >
        <CollapsibleTrigger asChild>
          <SidebarMenuButton>
            <ChevronRight className="transition-transform" />
            <Folder />
            {name}
            {layerInfo && <LayerBadge layer={layerInfo.layer} className="scale-75 ml-auto" />}
          </SidebarMenuButton>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <SidebarMenuSub>
            {files.map((fileItem, index) => (
              <Tree key={index} item={fileItem} onFileSelect={onFileSelect} />
            ))}
          </SidebarMenuSub>
        </CollapsibleContent>
      </Collapsible>
    </SidebarMenuItem>
  )
}
