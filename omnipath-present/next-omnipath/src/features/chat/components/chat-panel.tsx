"use client"

import { Message } from "ai"
import { useCallback, useRef, useState } from "react"
import { toast } from "sonner"
import { Message as PreviewMessage } from "./message"
import { useScrollToBottom } from "@/hooks/use-scroll-to-bottom"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { ScrollArea } from "@/components/ui/scroll-area"
import { ArrowUp, StopCircle, X as XIcon, Maximize2 } from "lucide-react"
import { useWindowSize } from "@/hooks/use-window-size"
import { ToolResult } from "./dual-mode-interface"

const suggestedActions = [
  {
    title: "Find interactions involving EGFR",
    label: "Search molecular interactions",
    action: "Find all interactions involving EGFR protein",
  },
  {
    title: "What is TP53?",
    label: "Search for entity information",
    action: "Tell me about TP53 - what kind of protein is it?",
  },
  {
    title: "Phosphorylation interactions",
    label: "Search by interaction type",
    action: "Show me phosphorylation interactions with strong evidence",
  },
]

interface ChatPanelProps {
  messages: Message[]
  input: string
  handleInputChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void
  handleSubmit: (e?: React.FormEvent<HTMLFormElement>) => void
  isLoading: boolean
  append: (message: Message) => void
  reload: () => void
  stop: () => void
  setMessages: (messages: Message[]) => void
  onToolResultClick: (result: ToolResult) => void
  mode: "chat" | "results"
  onMaximize?: () => void
}

export function ChatPanel({
  messages,
  input,
  handleInputChange,
  handleSubmit,
  isLoading,
  append,
  reload,
  stop,
  setMessages,
  onToolResultClick,
  mode,
  onMaximize,
}: ChatPanelProps) {
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null)
  const inputAreaRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const { width } = useWindowSize()

  const [messagesContainerRef, messagesEndRef] =
    useScrollToBottom<HTMLDivElement>(messages.length, messages[messages.length - 1]?.content)

  const startEdit = useCallback((messageId: string, currentContent: string) => {
    setEditingMessageId(messageId)
    // Create a synthetic event to update input
    const syntheticEvent = {
      target: { value: currentContent },
    } as React.ChangeEvent<HTMLTextAreaElement>
    handleInputChange(syntheticEvent)
    textareaRef.current?.focus()
  }, [handleInputChange])

  const cancelEdit = useCallback(() => {
    setEditingMessageId(null)
    const syntheticEvent = {
      target: { value: "" },
    } as React.ChangeEvent<HTMLTextAreaElement>
    handleInputChange(syntheticEvent)
  }, [handleInputChange])

  const handleEditAndRerun = useCallback((messageId: string, newContent: string) => {
    const messageIndex = messages.findIndex(msg => msg.id === messageId)
    if (messageIndex === -1) {
      console.error("Could not find message to edit:", messageId)
      toast.error("Failed to edit message. Please try again.")
      return
    }

    if (messages[messageIndex].role !== 'user') {
      console.error("Attempted to edit a non-user message:", messageId)
      toast.error("Cannot edit non-user messages.")
      return
    }

    const updatedHistory = messages.slice(0, messageIndex + 1)

    if (updatedHistory.length > 0) {
      updatedHistory[updatedHistory.length - 1] = {
        ...updatedHistory[updatedHistory.length - 1],
        content: newContent,
      }
    }

    setMessages(updatedHistory)
    reload()
    setEditingMessageId(null)
    const syntheticEvent = {
      target: { value: "" },
    } as React.ChangeEvent<HTMLTextAreaElement>
    handleInputChange(syntheticEvent)
  }, [messages, setMessages, handleInputChange, reload])

  const submitForm = useCallback((e?: React.FormEvent<HTMLFormElement>) => {
    e?.preventDefault()
    
    if (editingMessageId) {
      handleEditAndRerun(editingMessageId, input)
    } else {
      handleSubmit(e)
    }

    if (width && width > 768) {
      textareaRef.current?.focus()
    }
  }, [handleSubmit, width, editingMessageId, input, handleEditAndRerun])

  const editingIndex = editingMessageId ? messages.findIndex(m => m.id === editingMessageId) : -1

  return (
    <div className="h-full flex flex-col bg-background relative">
      {/* Floating maximize button for dual mode */}
      {mode === "results" && onMaximize && (
        <Button 
          variant="secondary" 
          size="icon"
          className="absolute top-0 right-2 z-10 shadow-md"
          onClick={onMaximize}
        >
          <Maximize2 className="w-4 h-4" />
        </Button>
      )}

      {/* Chat Messages */}
      {messages.length === 1 ? (
        <div className="flex-1 flex items-center justify-center p-4 overflow-auto">
          <div className="max-w-2xl w-full space-y-4">
            {messages.map((message) => (
              <div key={message.id}>
                <PreviewMessage
                  id={message.id}
                  role={message.role}
                  content={message.content}
                  toolInvocations={message.toolInvocations}
                  isInitialMessage={true}
                  startEdit={startEdit}
                  onToolResultClick={onToolResultClick}
                />
              </div>
            ))}
            
            <div className="space-y-4">
              <div className="relative">
                <Textarea
                  ref={textareaRef}
                  placeholder={editingMessageId ? "Edit message..." : "Send a message..."}
                  value={input}
                  onChange={handleInputChange}
                  className="min-h-[24px] overflow-hidden resize-none rounded-lg text-base bg-muted border-none pr-12"
                  rows={3}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault()
                      if (!isLoading) {
                        submitForm()
                      }
                    }
                  }}
                />

                {editingMessageId && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="absolute bottom-11 right-2 m-0.5 h-7 w-7"
                    onClick={cancelEdit}
                  >
                    <XIcon size={16} />
                  </Button>
                )}

                {isLoading ? (
                  <Button
                    className="rounded-full p-1.5 h-fit absolute bottom-2 right-2 m-0.5"
                    onClick={stop}
                    size="icon"
                  >
                    <StopCircle size={14} />
                  </Button>
                ) : (
                  <Button
                    className="rounded-full p-1.5 h-fit absolute bottom-2 right-2 m-0.5"
                    onClick={() => submitForm()}
                    disabled={input.length === 0}
                    size="icon"
                  >
                    <ArrowUp size={14} />
                  </Button>
                )}
              </div>

              <div className="grid sm:grid-cols-2 gap-4 w-full">
                {suggestedActions.map((suggestedAction, index) => (
                  <button
                    key={index}
                    onClick={() => {
                      append({
                        id: Date.now().toString(),
                        role: "user",
                        content: suggestedAction.action,
                      })
                    }}
                    className="bg-muted/50 w-full text-left rounded-lg p-3 text-sm hover:bg-muted transition-colors flex flex-col"
                  >
                    <span className="font-medium">{suggestedAction.title}</span>
                    <span className="text-muted-foreground">
                      {suggestedAction.label}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      ) : (
        <>
          <ScrollArea 
            ref={messagesContainerRef}
            className="flex-1 min-h-0 px-4 pt-4"
          >
            <div className="max-w-2xl mx-auto space-y-4">
              {messages.map((message, index) => (
                <div 
                  key={message.id} 
                  className={`${editingIndex !== -1 && index > editingIndex ? "opacity-50 pointer-events-none" : ""} transition-opacity duration-300`}
                >
                  <PreviewMessage
                    id={message.id}
                    role={message.role}
                    content={message.content}
                    toolInvocations={message.toolInvocations}
                    isInitialMessage={false}
                    startEdit={startEdit}
                    onToolResultClick={onToolResultClick}
                  />
                </div>
              ))}
              <div ref={messagesEndRef} className="h-4" />
            </div>
          </ScrollArea>

          {/* Input Area */}
          <div ref={inputAreaRef} className="px-4 pb-4 flex-shrink-0">
            <form onSubmit={submitForm} className="relative max-w-2xl mx-auto">
              <Textarea
                ref={textareaRef}
                placeholder={editingMessageId ? "Edit message..." : "Send a message..."}
                value={input}
                onChange={handleInputChange}
                className="min-h-[24px] overflow-hidden resize-none rounded-lg text-base bg-muted border-none pr-12"
                rows={3}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault()
                    if (!isLoading) {
                      submitForm()
                    }
                  }
                }}
              />

              {editingMessageId && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="absolute bottom-11 right-2 m-0.5 h-7 w-7"
                  onClick={cancelEdit}
                  type="button"
                >
                  <XIcon size={16} />
                </Button>
              )}

              {isLoading ? (
                <Button
                  className="rounded-full p-1.5 h-fit absolute bottom-2 right-2 m-0.5"
                  onClick={stop}
                  size="icon"
                  type="button"
                >
                  <StopCircle size={14} />
                </Button>
              ) : (
                <Button
                  className="rounded-full p-1.5 h-fit absolute bottom-2 right-2 m-0.5"
                  disabled={input.length === 0}
                  size="icon"
                  type="submit"
                >
                  <ArrowUp size={14} />
                </Button>
              )}
            </form>
          </div>
        </>
      )}
    </div>
  )
}