"use client"

import { useState } from "react"
import { MessageSquare } from "lucide-react"
import { Card, CardHeader, CardContent, CardTitle, CardDescription } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import Link from "next/link"
export function AIAssistantCard() {
  const [chatMessage, setChatMessage] = useState("")

  return (
    <Card className="md:w-1/2 transition-all duration-200 hover:shadow-md border-border/40">
      <Link href="/chat">
        <div className="absolute top-3 right-3">
          <Badge variant="secondary" className="text-xs px-2 py-0.5">
            Beta
          </Badge>
        </div>
        <CardHeader className="pb-4">
          <CardTitle className="text-lg font-medium">AI Assistant</CardTitle>
          <CardDescription className="text-sm text-muted-foreground leading-relaxed">
            Query the database using natural language to find proteins, interactions, and pathways
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            <div className="bg-muted/30 p-3 rounded-md text-sm text-muted-foreground">
              &ldquo;Show me all interactions involving p53&rdquo;
            </div>
            <div className="flex flex-col gap-2">
              <Input
                placeholder="Ask about molecular data..."
                value={chatMessage}
                onChange={(e) => setChatMessage(e.target.value)}
                disabled
                className="text-sm"
              />
              <Button variant="outline" className="w-full" disabled>
                <MessageSquare className="mr-2 h-3 w-3" />
                Try assistant
              </Button>
            </div>
          </div>
        </CardContent>
      </Link>
    </Card>
  )
}

