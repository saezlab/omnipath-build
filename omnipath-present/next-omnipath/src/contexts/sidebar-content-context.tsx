"use client"

import { createContext, useContext, ReactNode, useState } from "react"

interface SidebarContentContextType {
  sidebarContent: ReactNode | null
  setSidebarContent: (content: ReactNode | null) => void
}

const SidebarContentContext = createContext<SidebarContentContextType>({
  sidebarContent: null,
  setSidebarContent: () => {},
})

export function SidebarContentProvider({ children }: { children: ReactNode }) {
  const [sidebarContent, setSidebarContent] = useState<ReactNode | null>(null)

  return (
    <SidebarContentContext.Provider value={{ sidebarContent, setSidebarContent }}>
      {children}
    </SidebarContentContext.Provider>
  )
}

export function useSidebarContent() {
  return useContext(SidebarContentContext)
}
