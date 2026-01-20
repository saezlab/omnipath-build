"use client"

import { useEffect, useState } from "react"
import { Switch } from "@/components/ui/switch"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarSeparator,
  SidebarRail,
} from "@/components/ui/sidebar"
import {
  Search,
  MessageSquare,
  Sun,
  Moon,
  ListChecks,
  GitBranch,
} from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useTheme } from "next-themes"
import Image from "next/image"
import { useSidebarContent } from "@/contexts/sidebar-content-context"
import { useEntitySelection } from "@/contexts/entity-selection-context"
import { Badge } from "@/components/ui/badge"

const navigationItems = [
  {
    title: "Search",
    url: "/search",
    icon: Search,
  },
  {
    title: "Interactions",
    url: "/explore/interactions",
    icon: GitBranch,
  },
  {
    title: "Chat",
    url: "/chat",
    icon: MessageSquare,
  }
]

export function AppSidebar() {
  const pathname = usePathname()
  const { setTheme, resolvedTheme } = useTheme()
  const { sidebarContent } = useSidebarContent()
  const { selectionCount } = useEntitySelection()
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
  }, [])

  // Check if a path is active (exact match or parent match for explore routes)
  const isPathActive = (url: string) => {
    if (url.startsWith("/explore/")) {
      return pathname === url;
    }
    return pathname === url;
  }

  return (
    <Sidebar>
      <SidebarHeader className="border-b">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild>
              <Link href="/" className="flex items-center gap-2">
                <Image
                  src="/omnipath-logo-gradient.svg"
                  alt="OmniPath Logo"
                  width={40}
                  height={40}
                />
                <div className="grid flex-1 text-left text-sm leading-tight">
                  <span className="truncate font-bold text-lg bg-gradient-to-r from-[#007B7F] via-[#6EA945] to-[#FCCC06] bg-clip-text text-transparent">
                    OmniPath
                  </span>
                </div>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup className="px-2">
          <SidebarGroupContent>
            <SidebarMenu>
              {navigationItems.map((item) => (
                <SidebarMenuItem key={item.title}>
                  <SidebarMenuButton asChild isActive={isPathActive(item.url) || (item.url === "/search" && pathname === "/selection")}>
                    <Link href={item.url}>
                      <item.icon className="h-5 w-5" />
                      <span>{item.title}</span>
                    </Link>
                  </SidebarMenuButton>
                  {/* Selection submenu under Search */}
                  {item.url === "/search" && selectionCount > 0 && (
                    <SidebarMenuSub>
                      <SidebarMenuSubItem>
                        <SidebarMenuSubButton asChild isActive={pathname === "/selection"}>
                          <Link href="/selection" className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                              <ListChecks className="h-4 w-4" />
                              <span>Selection</span>
                            </div>
                            <Badge variant="secondary" className="ml-auto text-xs">
                              {selectionCount}
                            </Badge>
                          </Link>
                        </SidebarMenuSubButton>
                      </SidebarMenuSubItem>
                    </SidebarMenuSub>
                  )}
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        {/* Render filter sidebar on search, selection, explore, and sources pages */}
        {(pathname === '/search' || pathname === '/selection' || pathname.startsWith('/explore/') || pathname === '/sources') && sidebarContent && (
          <>
            <div className="px-3">
              <SidebarSeparator />
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto pb-4 px-4">
              {sidebarContent}
            </div>
          </>
        )}
      </SidebarContent>

      <SidebarFooter className="border-t">
        {/* Theme Toggle */}
        <div className="flex items-center justify-center px-4 py-2">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1">
              <Sun className="h-3 w-3 text-muted-foreground" />
              <span className="text-xs font-medium text-muted-foreground">Light</span>
            </div>
            {mounted ? (
              <Switch
                checked={resolvedTheme === "dark"}
                onCheckedChange={(checked) => {
                  if (checked) {
                    setTheme("dark")
                  } else {
                    setTheme("light")
                  }
                }}
                className="data-[state=checked]:bg-primary"
              />
            ) : (
              <div className="h-[1.15rem] w-8 rounded-full bg-input animate-pulse" />
            )}
            <div className="flex items-center gap-1">
              <span className="text-xs font-medium text-muted-foreground">Dark</span>
              <Moon className="h-3 w-3 text-muted-foreground" />
            </div>
          </div>
        </div>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
