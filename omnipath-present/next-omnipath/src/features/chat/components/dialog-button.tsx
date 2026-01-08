"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { 
  Dialog, 
  DialogContent, 
  DialogHeader, 
  DialogTitle,
  DialogDescription
} from "@/components/ui/dialog"
import * as VisuallyHidden from "@radix-ui/react-visually-hidden"
import { Badge } from "@/components/ui/badge"
import { ChevronRight } from "lucide-react"

interface DialogButtonProps {
  title: string
  description?: string
  buttonText?: string
  buttonVariant?: "default" | "secondary" | "outline" | "ghost" | "link" | "destructive"
  icon?: React.ReactNode
  badges?: Array<{ label: string; variant?: "default" | "secondary" | "outline" | "destructive" }>
  children: React.ReactNode
  fullScreen?: boolean
  className?: string
  dialogClassName?: string
}

export function DialogButton({
  title,
  description,
  buttonText = "Show Results",
  buttonVariant = "outline",
  icon,
  badges,
  children,
  fullScreen = false,
  className,
  dialogClassName
}: DialogButtonProps) {
  const [open, setOpen] = useState(false)

  return (
    <>
      <Button
        variant={buttonVariant}
        onClick={() => setOpen(true)}
        className={className}
      >
        {icon && <span className="mr-2">{icon}</span>}
        {buttonText}
        {badges && badges.length > 0 && (
          <div className="ml-2 flex gap-1">
            {badges.map((badge, index) => (
              <Badge key={index} variant={badge.variant || "secondary"} className="text-xs">
                {badge.label}
              </Badge>
            ))}
          </div>
        )}
        <ChevronRight className="ml-2 h-4 w-4" />
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent 
          className={
            fullScreen 
              ? "max-w-[98vw] w-screen h-[95vh] p-0 overflow-y-scroll max-h-screen" 
              : dialogClassName || "max-w-[80vw] max-h-[85vh]"
          }
        >
          {fullScreen ? (
            <VisuallyHidden.Root>
              <DialogTitle>{title}</DialogTitle>
            </VisuallyHidden.Root>
          ) : (
            <DialogHeader className="px-6 py-4 border-b">
              <DialogTitle>{title}</DialogTitle>
              {description && (
                <DialogDescription>{description}</DialogDescription>
              )}
            </DialogHeader>
          )}
          <div className={fullScreen ? "h-full w-full" : "p-6 overflow-auto max-h-[calc(85vh-8rem)]"}>
            {children}
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}