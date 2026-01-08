"use client"

import { cn } from "@/lib/utils"
import { MessageSquare, Search, Network, Database } from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"

export function MainNav() {
  const pathname = usePathname()

  return (
    <nav className="flex items-center space-x-4 lg:space-x-6">
      <Link
        href="/search"
        className={cn(
          "flex items-center gap-1 text-sm font-medium transition-colors hover:text-primary",
          pathname === "/search" ? "text-primary" : "text-muted-foreground",
        )}
      >
        <Search className="h-4 w-4" />
        <span className="hidden sm:inline">Search</span>
      </Link>
      <Link
        href="/interactions/search"
        className={cn(
          "flex items-center gap-1 text-sm font-medium transition-colors hover:text-primary",
          pathname === "/interactions/search" ? "text-primary" : "text-muted-foreground",
        )}
      >
        <Network className="h-4 w-4" />
        <span className="hidden sm:inline">Interactions</span>
      </Link>
      <Link
        href="/sources"
        className={cn(
          "flex items-center gap-1 text-sm font-medium transition-colors hover:text-primary",
          pathname === "/sources" ? "text-primary" : "text-muted-foreground",
        )}
      >
        <Database className="h-4 w-4" />
        <span className="hidden sm:inline">Datasources</span>
      </Link>
      <Link
        href="/chat"
        className={cn(
          "flex items-center gap-1 text-sm font-medium transition-colors hover:text-primary",
          pathname === "/chat" ? "text-primary" : "text-muted-foreground",
        )}
      >
        <MessageSquare className="h-4 w-4" />
        <span className="hidden sm:inline">Chat</span>
      </Link>
    </nav>
  )
}

