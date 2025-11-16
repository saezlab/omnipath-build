import type { ReactNode } from "react"
import { SiteHeader } from "@/components/layout/site-header"
import { SiteFooter } from "@/components/layout/site-footer"

interface SiteLayoutProps {
  children: ReactNode
  showFooter?: boolean
}

export function SiteLayout({ children, showFooter = false }: SiteLayoutProps) {
  return (
    <div className="flex flex-col min-h-screen">
      <SiteHeader />
      <main className="flex-1">{children}</main>
      {showFooter && <SiteFooter />}
    </div>
  )
}

