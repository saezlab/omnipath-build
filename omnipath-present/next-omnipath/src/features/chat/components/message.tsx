"use client";

import { ToolInvocation } from "ai";
import { motion } from "framer-motion";
import { ReactNode } from "react";

import { Markdown } from "./markdown";
import { ToolResponse } from "./tool-response";
import { Button } from "@/components/ui/button";
import { Pencil } from "lucide-react";
import type { ToolResult } from "./dual-mode-interface";

// Extend the ToolInvocation type locally if needed, or handle potential errors defensively.
// This assumes result might contain an error property.
type ExtendedToolInvocation = ToolInvocation & { result?: { error?: string } };

const ToolDetails = ({
  toolInvocation,
  onToolResultClick,
  messageId,
}: {
  toolInvocation: ExtendedToolInvocation;
  onToolResultClick?: (result: ToolResult) => void;
  messageId: string;
}) => {
  const { state, result } = toolInvocation;

  // Only show the tool response, not the header
  if (state === "result" && result) {
    return <ToolResponse toolInvocation={toolInvocation} onToolResultClick={onToolResultClick} messageId={messageId} />;
  }

  // Show loading state if still running
  if (state === "call") {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <div className="h-2 w-2 rounded-full bg-blue-500 animate-pulse" />
        Running...
      </div>
    );
  }

  return null;
};

export const Message = ({
  role,
  content,
  toolInvocations,
  isInitialMessage,
  id,
  startEdit,
  onToolResultClick
}: {
  role: string;
  content: string | ReactNode;
  toolInvocations: Array<ToolInvocation> | undefined;
  isInitialMessage?: boolean;
  id: string;
  startEdit?: (messageId: string, currentContent: string) => void;
  onToolResultClick?: (result: ToolResult) => void;
}) => {
  const canEdit = role === 'user' && typeof content === 'string' && !!startEdit;

  return (
    <motion.div
      className={`flex flex-row gap-4 px-4 w-full md:px-0 mb-8 ${
        role === "user" ? "justify-end" : "justify-start"
      } ${isInitialMessage ? "text-center" : ""}`}
      initial={{ y: 5, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
    >
      <div className={`flex flex-col gap-4 ${role === "user" ? "w-[66%] items-end" : "w-full items-start"} ${isInitialMessage ? "items-center" : ""}`}>
        {toolInvocations && toolInvocations.length > 0 && (
          <div className="flex flex-col gap-3 w-full max-w-2xl">
            {toolInvocations.map((toolInvocation) => (
              <ToolDetails 
                key={toolInvocation.toolCallId} 
                toolInvocation={toolInvocation} 
                onToolResultClick={onToolResultClick}
                messageId={id}
              />
            ))}
          </div>
        )}

        {content && typeof content === "string" && (
          <div className={`relative group text-zinc-800 dark:text-zinc-300 flex flex-col gap-4 w-full ${
            role === "user" 
              ? "bg-zinc-100 dark:bg-zinc-800 rounded-lg p-3" 
              : isInitialMessage 
                ? "text-lg font-medium" 
                : ""
          }`}>
            <Markdown>{content as string}</Markdown>
            {canEdit && (
               <Button 
                  variant="ghost" 
                  size="icon" 
                  onClick={() => startEdit(id, content as string)}
                  className="absolute top-1 right-1 h-6 w-6 text-zinc-400 dark:text-zinc-500 opacity-0 group-hover:opacity-100 transition-opacity duration-150"
                  aria-label="Edit message"
                  data-edit-button="true"
                >
                  <Pencil className="h-3 w-3" />
                </Button>
            )}
          </div>
        )}
      </div>
    </motion.div>
  );
}; 