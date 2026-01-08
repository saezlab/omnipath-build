import type { ReactNode } from "react"
import Link from "next/link"
import { ArrowRight } from "lucide-react"
import { Card, CardHeader, CardContent, CardFooter, CardTitle, CardDescription } from "@/components/ui/card"
import { Button } from "@/components/ui/button"

interface FeatureCardProps {
  title: string
  description: string
  features: Array<{
    icon: ReactNode
    title: string
    description: string
  }>
  href: string
  buttonText: string
}

export function FeatureCard({  title, description, features, href, buttonText }: FeatureCardProps) {
  return (
    <Card className="group relative overflow-hidden transition-all duration-200 hover:shadow-md border-border/40">
      <CardHeader className="pb-4">
        <CardTitle className="text-lg font-medium">{title}</CardTitle>
        <CardDescription className="text-sm text-muted-foreground leading-relaxed">{description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-2">
          {features.map((feature, index) => (
            <div key={index} className="flex items-start gap-2 text-sm">
              <div className="w-4 h-4 mt-0.5 text-muted-foreground/60 flex-shrink-0">
                {feature.icon}
              </div>
              <div>
                <span className="font-medium text-foreground">{feature.title}</span>
                <span className="text-muted-foreground ml-1">— {feature.description}</span>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
      <CardFooter>
        <Button variant="outline" className="w-full" asChild>
          <Link href={href}>
            {buttonText}
            <ArrowRight className="ml-2 h-3 w-3" />
          </Link>
        </Button>
      </CardFooter>
    </Card>
  )
}

