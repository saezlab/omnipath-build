import React from "react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Badge } from "@/components/ui/badge";
import { ChevronDown } from "lucide-react";

interface IdentifiersPopoverProps {
  identifiers?: string[];
  synonyms?: string[];
  type: 'entity' | 'cv_term';
}

export function IdentifiersPopover({ identifiers, synonyms, type }: IdentifiersPopoverProps) {
  const hasIdentifiers = identifiers && identifiers.length > 0;
  const hasSynonyms = synonyms && synonyms.length > 0;
  
  if (!hasIdentifiers && !hasSynonyms) {
    return null;
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors">
          <span>View all {type === 'entity' ? 'identifiers' : 'synonyms'}</span>
          <ChevronDown className="h-3 w-3" />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-80 max-h-96 overflow-y-auto" align="start">
        <div className="space-y-3">
          {hasIdentifiers && (
            <div>
              <h4 className="text-sm font-medium mb-2">Identifiers</h4>
              <div className="flex flex-wrap gap-1">
                {identifiers.map((id) => (
                  <Badge key={id} variant="outline" className="text-xs">
                    {id}
                  </Badge>
                ))}
              </div>
            </div>
          )}
          
          {hasSynonyms && (
            <div>
              <h4 className="text-sm font-medium mb-2">Synonyms</h4>
              <div className="flex flex-wrap gap-1">
                {synonyms.map((syn) => (
                  <Badge key={syn} variant="outline" className="text-xs">
                    {syn}
                  </Badge>
                ))}
              </div>
            </div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}