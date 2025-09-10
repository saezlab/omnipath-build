"use client"

import { useState } from "react"
import {
  ChevronRight,
  ChevronDown,
  Database,
  Folder,
  File,
  FileText,
  Eye,
  Download,
} from "lucide-react"
import { useDatabase } from "@/hooks/use-database-context"
import Link from "next/link"
import LayerBadge from "../app/components/LayerBadge"

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  useSidebar,
} from "@/components/ui/sidebar"

interface TreeNode {
  name: string
  path: string
  type: 'database' | 'layer' | 'folder' | 'file'
  layer?: 'bronze' | 'silver' | 'gold'
  children?: TreeNode[]
  size?: number
}

function TreeItem({ 
  node, 
  onFileSelect, 
  depth = 0 
}: { 
  node: TreeNode
  onFileSelect?: (path: string) => void
  depth?: number 
}) {
  const [isExpanded, setIsExpanded] = useState(depth < 2)
  const { isMobile } = useSidebar()

  const handleClick = () => {
    if (node.type === 'file') {
      onFileSelect?.(node.path)
    } else if (node.children) {
      setIsExpanded(!isExpanded)
    }
  }

  const getIcon = () => {
    if (node.type === 'database') return <Database className="w-4 h-4" />
    if (node.type === 'folder' || node.type === 'layer') return <Folder className="w-4 h-4" />
    return <FileText className="w-4 h-4" />
  }

  const formatSize = (bytes?: number) => {
    if (!bytes) return ''
    const mb = bytes / (1024 * 1024)
    return mb > 1 ? `${mb.toFixed(1)}MB` : `${(bytes / 1024).toFixed(1)}KB`
  }

  if (node.children && node.children.length > 0) {
    return (
      <Collapsible open={isExpanded} onOpenChange={setIsExpanded}>
        <SidebarMenuItem>
          <CollapsibleTrigger asChild>
            <SidebarMenuButton 
              className="w-full justify-start"
              style={{ paddingLeft: `${depth * 0.75 + 0.5}rem` }}
            >
              {isExpanded ? 
                <ChevronDown className="w-4 h-4 text-muted-foreground" /> : 
                <ChevronRight className="w-4 h-4 text-muted-foreground" />
              }
              {getIcon()}
              <span className="flex-1 text-left truncate">{node.name}</span>
              {node.layer && <LayerBadge layer={node.layer} className="scale-75" />}
              {node.children && (
                <span className="text-xs text-muted-foreground">
                  {node.children.length}
                </span>
              )}
            </SidebarMenuButton>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <SidebarMenuSub>
              {node.children.map((child) => (
                <TreeItem 
                  key={child.path} 
                  node={child} 
                  onFileSelect={onFileSelect} 
                  depth={depth + 1} 
                />
              ))}
            </SidebarMenuSub>
          </CollapsibleContent>
        </SidebarMenuItem>
      </Collapsible>
    )
  }

  // Leaf node (file)
  return (
    <SidebarMenuItem>
      <SidebarMenuButton 
        className="w-full justify-start text-blue-600 dark:text-blue-400"
        style={{ paddingLeft: `${depth * 0.75 + 1.25}rem` }}
        onClick={handleClick}
      >
        <File className="w-4 h-4" />
        <span className="flex-1 text-left truncate">{node.name}</span>
        {node.size && (
          <span className="text-xs text-muted-foreground">
            {formatSize(node.size)}
          </span>
        )}
      </SidebarMenuButton>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <SidebarMenuAction showOnHover>
            <Eye />
            <span className="sr-only">View file</span>
          </SidebarMenuAction>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          className="w-48 rounded-lg"
          side={isMobile ? "bottom" : "right"}
          align={isMobile ? "end" : "start"}
        >
          <DropdownMenuItem onClick={() => onFileSelect?.(node.path)}>
            <Eye className="text-muted-foreground" />
            <span>View Data</span>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem>
            <Download className="text-muted-foreground" />
            <span>Download File</span>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </SidebarMenuItem>
  )
}

export function NavDatabaseTree() {
  const { selectedDatabase, loading } = useDatabase()
  
  if (loading) {
    return (
      <SidebarGroup className="group-data-[collapsible=icon]:hidden">
        <SidebarGroupLabel>Database Structure</SidebarGroupLabel>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton disabled>
              <FileText className="w-4 h-4 animate-pulse" />
              <span>Loading...</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarGroup>
    )
  }

  if (!selectedDatabase) {
    return (
      <SidebarGroup className="group-data-[collapsible=icon]:hidden">
        <SidebarGroupLabel>Database Structure</SidebarGroupLabel>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton disabled>
              <Database className="w-4 h-4 opacity-50" />
              <span className="text-muted-foreground">No database selected</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarGroup>
    )
  }

  const handleFileSelect = (path: string) => {
    // Navigate to file view or trigger file viewer
    console.log('Selected file:', path)
    // Could implement routing to a file viewer page
  }

  const databaseTree: TreeNode = {
    name: selectedDatabase.name,
    path: selectedDatabase.path,
    type: 'database',
    children: [
      {
        name: 'Bronze Layer',
        path: `${selectedDatabase.path}/bronze`,
        type: 'layer',
        layer: 'bronze',
        children: selectedDatabase.layers.bronze.map(file => ({
          name: file.name,
          path: file.path,
          type: 'file' as const,
          size: file.size
        }))
      },
      {
        name: 'Silver Layer', 
        path: `${selectedDatabase.path}/silver`,
        type: 'layer',
        layer: 'silver',
        children: selectedDatabase.layers.silver.map(file => ({
          name: file.name,
          path: file.path,
          type: 'file' as const,
          size: file.size
        }))
      },
      {
        name: 'Gold Layer',
        path: `${selectedDatabase.path}/gold`, 
        type: 'layer',
        layer: 'gold',
        children: selectedDatabase.layers.gold.map(file => ({
          name: file.name,
          path: file.path,
          type: 'file' as const,
          size: file.size
        }))
      }
    ].filter(layer => layer.children && layer.children.length > 0)
  }

  return (
    <SidebarGroup className="group-data-[collapsible=icon]:hidden">
      <SidebarGroupLabel>Database Structure</SidebarGroupLabel>
      <SidebarMenu>
        <TreeItem node={databaseTree} onFileSelect={handleFileSelect} />
      </SidebarMenu>
    </SidebarGroup>
  )
}
