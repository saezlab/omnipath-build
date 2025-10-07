"use client"

import * as React from "react"
import {
  Home,
} from "lucide-react"

import { NavMain } from "@/components/nav-main"
import { NavUser } from "@/components/nav-user"
import { DatabaseSwitcher } from "@/components/team-switcher"
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
      url: "/",
      icon: Home,
      isActive: true,
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
