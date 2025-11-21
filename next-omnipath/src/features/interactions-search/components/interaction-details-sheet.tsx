"use client"

import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet"
import { InteractionDetails } from "@/features/interactions-search/components/interaction-details"
import { MeilisearchInteraction } from "@/types/meilisearch"

interface InteractionDetailsSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  interaction: MeilisearchInteraction | null
}

export function InteractionDetailsSheet({ open, onOpenChange, interaction }: InteractionDetailsSheetProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full sm:max-w-2xl overflow-y-auto pb-8">
        <SheetHeader>
          <SheetTitle>Interaction Details</SheetTitle>
          <SheetDescription>
            View detailed evidence for this interaction
          </SheetDescription>
        </SheetHeader>

        <div className="mt-6 mb-6">
          {/* Evidence is now embedded in the interaction document */}
          <InteractionDetails selectedInteraction={interaction} />
        </div>
      </SheetContent>
    </Sheet>
  )
}
