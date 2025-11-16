"use client";

import { DualModeInterface } from "./components/dual-mode-interface";
import { SiteLayout } from "@/components/layout/main-layout";
import { Message } from "ai";
import { useChat } from "ai/react";

export default function ChatPage() {
  const initialMessages: Message[] = [
    {
      id: "1",
      role: "assistant",
      content: "Hello! I'm OmniPath AI. I can help you explore protein interactions, pathways, and biological annotations. What would you like to know?",
    },
  ];

  const {
    messages,
    input,
    handleInputChange,
    handleSubmit,
    append,
    isLoading,
    stop,
    setMessages,
    reload,
  } = useChat({
    id: "main-chat",
    initialMessages,
    maxSteps: 10,
  });

  return (
    <SiteLayout>
      <div className="flex-1 flex flex-col" style={{ height: 'calc(100vh - 4rem)' }}>
        <DualModeInterface
          messages={messages}
          input={input}
          handleInputChange={handleInputChange}
          handleSubmit={handleSubmit}
          isLoading={isLoading}
          append={append}
          reload={reload}
          stop={stop}
          setMessages={setMessages}
        />
      </div>
    </SiteLayout>
  );
}