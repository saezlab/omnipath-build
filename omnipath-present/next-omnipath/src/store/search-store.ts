import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { nanoid } from 'nanoid'
import { ToolInvocation } from 'ai'
import { MeilisearchInteraction } from '@/types/meilisearch'

export interface InteractionsFilters {
  interactionType: string[]
  detectionMethods: string[]
  causalMechanism: string[]
  causalStatement: string[]
  entityTypeSource: string[]
  entityTypeTarget: string[]
  isDirected: boolean | null
  isStimulation: boolean | null
  isInhibition: boolean | null
  isUpstream: boolean | null
  isDownstream: boolean | null
  minReferences: number | null
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system' | 'function' | 'tool'
  content: string
  toolInvocations?: ToolInvocation[]
}

export interface ChatSession {
  id: string
  messages: ChatMessage[]
}

interface SearchState {
  // Interactions state
  interactionsQuery: string
  interactionsResults: MeilisearchInteraction[]
  interactionsCurrentPage: number
  selectedInteraction: MeilisearchInteraction | null
  interactionsFilters: InteractionsFilters

  // Chat state
  chats: ChatSession[]
  currentChatId: string | null
  messages: ChatMessage[]

  // Actions
  setInteractionsQuery: (query: string) => void
  setInteractionsResults: (results: MeilisearchInteraction[]) => void
  setInteractionsCurrentPage: (page: number) => void
  setSelectedInteraction: (interaction: MeilisearchInteraction | null) => void
  setInteractionsFilters: (filters: InteractionsFilters | ((prev: InteractionsFilters) => InteractionsFilters)) => void

  // Chat Actions
  addMessage: (message: ChatMessage) => void
  setMessages: (messages: ChatMessage[]) => void
  startNewChat: () => void
  switchChat: (chatId: string) => void
  saveCurrentChat: () => void
}

const initialChatId = nanoid()

export const useSearchStore = create<SearchState>()(
  persist(
    (set) => ({
      // Initial state
      interactionsQuery: '',
      interactionsResults: [],
      interactionsCurrentPage: 1,
      selectedInteraction: null,
      interactionsFilters: {
        interactionType: [],
        detectionMethods: [],
        causalMechanism: [],
        causalStatement: [],
        entityTypeSource: [],
        entityTypeTarget: [],
        isDirected: null,
        isStimulation: null,
        isInhibition: null,
        isUpstream: null,
        isDownstream: null,
        minReferences: 0,
      },
      // Chat initial state
      chats: [{ id: initialChatId, messages: [] }],
      currentChatId: initialChatId,
      messages: [],
      // Actions
      setInteractionsQuery: (query: string) => set({ interactionsQuery: query }),
      setInteractionsResults: (results: MeilisearchInteraction[]) => set({ interactionsResults: results }),
      setInteractionsCurrentPage: (page: number) => set({ interactionsCurrentPage: page }),
      setSelectedInteraction: (interaction: MeilisearchInteraction | null) => set({ selectedInteraction: interaction }),
      setInteractionsFilters: (filters: InteractionsFilters | ((prev: InteractionsFilters) => InteractionsFilters)) => 
        set((state: SearchState) => ({ 
          interactionsFilters: typeof filters === 'function' ? filters(state.interactionsFilters) : filters 
        })),
      // Chat Actions
      addMessage: (message: ChatMessage) => set((state) => ({ messages: [...state.messages, message] })),
      setMessages: (messages: ChatMessage[]) => set({ messages }),
      startNewChat: () => {
        const newId = nanoid();
        set((state) => ({
          chats: [...state.chats, { id: newId, messages: [] }],
          currentChatId: newId,
          messages: [],
        }));
      },
      switchChat: (chatId: string) => set({ currentChatId: chatId }),
      saveCurrentChat: () => {},
    }),
    {
      name: 'search-store',
      partialize: (state) => ({
        interactionsQuery: state.interactionsQuery,
        interactionsCurrentPage: state.interactionsCurrentPage,
        selectedInteraction: state.selectedInteraction,
        interactionsFilters: state.interactionsFilters,
        chats: state.chats,
        currentChatId: state.currentChatId,
      }),
    }
  )
) 