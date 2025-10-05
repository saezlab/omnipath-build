"use client"

import * as React from "react"
import {
  Home,
  Database,
  Layers,
  FileText,
  Settings2,
  User,
} from "lucide-react"

import { NavMain } from "@/components/nav-main"
import { NavDatabaseTree } from "@/components/nav-projects"
import { NavUser } from "@/components/nav-user"
import { DatabaseSwitcher } from "@/components/team-switcher"
import { DatabaseProvider } from "@/hooks/use-database-context"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarRail,
} from "@/components/ui/sidebar"

// OmniPath specific navigation data
const navigationData = {
  user: {
    name: "OmniPath User",
    email: "user@omnipath.local",
    avatar: "/avatars/omnipath.jpg",
  },
  navMain: [
    {
      title: "Dashboard",
      url: "/dashboard",
      icon: Home,
      isActive: true,
    },
    {
      title: "Databases",
      url: "/databases",
      icon: Database,
      items: [], // Will be populated dynamically with actual databases
    },
    {
      title: "File Explorer",
      url: "/explorer",
      icon: FileText,
      items: [
        {
          title: "Bronze Layer",
          url: "/explorer/bronze",
        },
        {
          title: "Silver Layer",
          url: "/explorer/silver",
        },
        {
          title: "Gold Layer",
          url: "/explorer/gold",
        },
      ],
    },
    {
      title: "Pipeline",
      url: "/pipeline",
      icon: Layers,
      items: [
        {
          title: "Data Sources",
          url: "/pipeline/sources",
        },
        {
          title: "Transformations",
          url: "/pipeline/transforms",
        },
        {
          title: "Output Targets",
          url: "/pipeline/targets",
        },
      ],
    },
    {
      title: "Settings",
      url: "/settings",
      icon: Settings2,
      items: [
        {
          title: "General",
          url: "/settings/general",
        },
        {
          title: "Data Sources",
          url: "/settings/sources",
        },
        {
          title: "Performance",
          url: "/settings/performance",
        },
      ],
    },
  ],
}

function AppSidebarContent({ ...props }: React.ComponentProps<typeof Sidebar>) {
  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader>
        <DatabaseSwitcher />
      </SidebarHeader>
      <SidebarContent>
        <NavMain items={navigationData.navMain} />
        <NavDatabaseTree />
      </SidebarContent>
      <SidebarFooter>
        <NavUser user={navigationData.user} />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  return <AppSidebarContent {...props} />
}
