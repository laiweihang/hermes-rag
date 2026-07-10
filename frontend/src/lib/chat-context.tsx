"use client";

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  type ReactNode,
} from "react";
import { get, getToken, post, put, del } from "@/lib/api";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface Conversation {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
  message_count?: number;
  skill_id?: number | null;
}

export interface Skill {
  id: number;
  name: string;
  description: string;
  icon: string;
}

export type ModelType = "llm" | "embedding" | "ocr";

export interface LlmProvider {
  id: number;
  name: string;
  model_type: ModelType;
  provider_type: "local" | "remote";
  base_url: string;
  api_key_hint: string;
  model_name: string;
  max_tokens: number;
  timeout_seconds: number;
  is_default: boolean;
  is_active: boolean;
}

export interface Message {
  id: number;
  role: "user" | "assistant";
  content: string;
  sources?: string | null;
  rule_matched?: string | null;
  // 查询重写信息：JSON 字符串，由后端 [REWRITE] SSE 事件 / MessageOut 字段
  // 透传。形如 {"original":"","simple":""|null,"hyde":""|null,
  // "bm25_query":"","vector_query":"","simple_enabled":bool,"hyde_enabled":bool}
  query_rewrite?: string | null;
  created_at: string;
}

interface ConversationDetail {
  id: number;
  title: string;
  skill_id?: number | null;
  created_at: string;
  updated_at: string;
  messages: Message[];
}

interface ConversationsResponse {
  conversations: Conversation[];
}

/* ------------------------------------------------------------------ */
/*  Module-level fetchers (outside component scope)                    */
/*  React Compiler won't inject these as useEffect deps                */
/* ------------------------------------------------------------------ */

async function loadConversations(
  setter: React.Dispatch<React.SetStateAction<Conversation[]>>,
) {
  if (!getToken()) return;
  try {
    const data = await get<ConversationsResponse>("/api/conversations");
    setter(data.conversations ?? []);
  } catch {
    /* silent */
  }
}

async function loadSkills(
  setter: React.Dispatch<React.SetStateAction<Skill[]>>,
) {
  if (!getToken()) return;
  try {
    const data = await get<{ skills: Skill[] }>("/api/skills");
    setter(data.skills ?? []);
  } catch {
    /* silent */
  }
}

async function loadProvidersByType(
  modelType: ModelType,
  setter: React.Dispatch<React.SetStateAction<LlmProvider[]>>,
) {
  if (!getToken()) return;
  try {
    const data = await get<{ providers: LlmProvider[] }>(
      `/api/providers?model_type=${modelType}`,
    );
    setter(data.providers ?? []);
  } catch {
    /* silent */
  }
}

async function loadDocumentCount(
  setter: React.Dispatch<React.SetStateAction<number>>,
) {
  if (!getToken()) return;
  try {
    const data = await get<{ documents: number; chunks: number }>(
      "/api/knowledge/status",
    );
    setter(data.documents ?? 0);
  } catch {
    /* silent */
  }
}

/* ------------------------------------------------------------------ */
/*  Context value                                                      */
/* ------------------------------------------------------------------ */

interface ChatContextValue {
  conversations: Conversation[];
  activeConversationId: number | null;
  messages: Message[];
  loadingMessages: boolean;
  setActiveConversationId: (id: number | null) => void;
  createConversation: () => Promise<Conversation | null>;
  deleteConversation: (id: number) => Promise<void>;
  refreshConversations: () => Promise<void>;
  updateConversationTitle: (id: number, title: string) => void;

  skills: Skill[];
  activeSkillId: number | null;
  setActiveSkillId: (id: number | null) => void;
  refreshSkills: () => Promise<void>;

  /** 用户在"对话页"为当前对话临时切换的 LLM Provider */
  providers: LlmProvider[];
  activeProviderId: number | null;
  setActiveProviderId: (id: number | null) => void;
  refreshProviders: () => Promise<void>;

  /** 嵌入 / OCR 类型 Provider 列表（管理后台或文档页使用） */
  embeddingProviders: LlmProvider[];
  ocrProviders: LlmProvider[];
  refreshEmbeddingProviders: () => Promise<void>;
  refreshOcrProviders: () => Promise<void>;

  /** 管理员：把某个 Provider 设为对应类型的默认 */
  setDefaultProvider: (id: number) => Promise<boolean>;

  /** 是否管理员（基于 JWT role 字段） */
  isAdmin: boolean;

  /** 是否结合知识库 RAG（发送消息时传给后端） */
  useRag: boolean;
  setUseRag: (v: boolean) => void;

  /** 已入库文档数量（用于「参考文档」旁展示） */
  documentCount: number;
  refreshDocumentCount: () => Promise<void>;
  /** 与刚拉取的文档列表长度同步，避免重复请求 /api/documents */
  syncDocumentCountFromLength: (n: number) => void;

  isStreaming: boolean;
  setIsStreaming: (v: boolean) => void;

  appendMessage: (msg: Message) => void;
  updateLastAssistantContent: (content: string) => void;
  updateLastAssistantSources: (sources: string) => void;
  updateLastAssistantQueryRewrite: (rewriteJson: string) => void;
}

const ChatContext = createContext<ChatContextValue | null>(null);

export function useChatContext() {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChatContext must be used within ChatProvider");
  return ctx;
}

/* ------------------------------------------------------------------ */
/*  Provider                                                           */
/* ------------------------------------------------------------------ */

function parseJwtRole(token: string): string | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const decoded = atob(parts[1].replace(/-/g, "+").replace(/_/g, "/"));
    const payload = JSON.parse(decoded) as Record<string, unknown>;
    return typeof payload.role === "string" ? payload.role : null;
  } catch {
    return null;
  }
}

export function ChatProvider({ children }: { children: ReactNode }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [providers, setProviders] = useState<LlmProvider[]>([]);
  const [embeddingProviders, setEmbeddingProviders] = useState<LlmProvider[]>([]);
  const [ocrProviders, setOcrProviders] = useState<LlmProvider[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<number | null>(null);
  const [activeSkillId, setActiveSkillId] = useState<number | null>(null);
  const [activeProviderId, setActiveProviderId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);
  const [useRag, setUseRag] = useState(true);
  const [documentCount, setDocumentCount] = useState(0);

  useEffect(() => {
    const token = getToken();
    if (token) {
      setIsAdmin(parseJwtRole(token) === "admin");
    }
    loadConversations(setConversations);
    loadSkills(setSkills);
    loadProvidersByType("llm", setProviders);
    loadProvidersByType("embedding", setEmbeddingProviders);
    loadProvidersByType("ocr", setOcrProviders);
    loadDocumentCount(setDocumentCount);
  }, []);

  const refreshDocumentCount = useCallback(
    () => loadDocumentCount(setDocumentCount),
    [],
  );

  const syncDocumentCountFromLength = useCallback((n: number) => {
    setDocumentCount(n);
  }, []);

  const refreshConversations = useCallback(
    () => loadConversations(setConversations),
    [],
  );

  const refreshSkills = useCallback(
    () => loadSkills(setSkills),
    [],
  );

  const refreshProviders = useCallback(
    () => loadProvidersByType("llm", setProviders),
    [],
  );

  const refreshEmbeddingProviders = useCallback(
    () => loadProvidersByType("embedding", setEmbeddingProviders),
    [],
  );

  const refreshOcrProviders = useCallback(
    () => loadProvidersByType("ocr", setOcrProviders),
    [],
  );

  const setDefaultProvider = useCallback(async (id: number): Promise<boolean> => {
    try {
      await put(`/api/admin/providers/${id}`, { is_default: true });
      // 刷新三类，使前端的 is_default 标记同步
      await Promise.all([
        loadProvidersByType("llm", setProviders),
        loadProvidersByType("embedding", setEmbeddingProviders),
        loadProvidersByType("ocr", setOcrProviders),
      ]);
      return true;
    } catch {
      return false;
    }
  }, []);

  /* ---- load messages when active conversation changes ---- */
  useEffect(() => {
    if (!activeConversationId) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    (async () => {
      setLoadingMessages(true);
      try {
        const data = await get<ConversationDetail>(
          `/api/conversations/${activeConversationId}`
        );
        if (!cancelled) {
          setMessages(data.messages ?? []);
        }
      } catch {
        if (!cancelled) setMessages([]);
      } finally {
        if (!cancelled) setLoadingMessages(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeConversationId]);

  /* ---- create conversation ---- */
  const createConversation = useCallback(async (): Promise<Conversation | null> => {
    try {
      const body: Record<string, unknown> = {};
      if (activeSkillId) body.skill_id = activeSkillId;
      if (activeProviderId) body.provider_id = activeProviderId;
      const data = await post<Conversation>("/api/conversations", body);
      setConversations((prev) => [data, ...prev]);
      setActiveConversationId(data.id);
      return data;
    } catch {
      return null;
    }
  }, [activeSkillId, activeProviderId]);

  /* ---- delete conversation ---- */
  const deleteConversation = useCallback(
    async (id: number) => {
      try {
        await del(`/api/conversations/${id}`);
        setConversations((prev) => prev.filter((c) => c.id !== id));
        if (activeConversationId === id) {
          setActiveConversationId(null);
          setMessages([]);
        }
      } catch {
        /* silent */
      }
    },
    [activeConversationId]
  );

  /* ---- optimistic helpers ---- */
  const appendMessage = useCallback((msg: Message) => {
    setMessages((prev) => [...prev, msg]);
  }, []);

  const updateLastAssistantContent = useCallback((content: string) => {
    setMessages((prev) => {
      const copy = [...prev];
      for (let i = copy.length - 1; i >= 0; i--) {
        if (copy[i].role === "assistant") {
          copy[i] = { ...copy[i], content };
          break;
        }
      }
      return copy;
    });
  }, []);

  const updateLastAssistantSources = useCallback((sources: string) => {
    setMessages((prev) => {
      const copy = [...prev];
      for (let i = copy.length - 1; i >= 0; i--) {
        if (copy[i].role === "assistant") {
          copy[i] = { ...copy[i], sources };
          break;
        }
      }
      return copy;
    });
  }, []);

  const updateLastAssistantQueryRewrite = useCallback((rewriteJson: string) => {
    setMessages((prev) => {
      const copy = [...prev];
      for (let i = copy.length - 1; i >= 0; i--) {
        if (copy[i].role === "assistant") {
          copy[i] = { ...copy[i], query_rewrite: rewriteJson };
          break;
        }
      }
      return copy;
    });
  }, []);

  const updateConversationTitle = useCallback((id: number, title: string) => {
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, title } : c))
    );
  }, []);

  return (
    <ChatContext.Provider
      value={{
        conversations,
        activeConversationId,
        messages,
        loadingMessages,
        setActiveConversationId,
        createConversation,
        deleteConversation,
        refreshConversations,
        updateConversationTitle,
        skills,
        activeSkillId,
        setActiveSkillId,
        refreshSkills,
        providers,
        activeProviderId,
        setActiveProviderId,
        refreshProviders,
        embeddingProviders,
        ocrProviders,
        refreshEmbeddingProviders,
        refreshOcrProviders,
        setDefaultProvider,
        isAdmin,
        useRag,
        setUseRag,
        documentCount,
        refreshDocumentCount,
        syncDocumentCountFromLength,
        isStreaming,
        setIsStreaming,
        appendMessage,
        updateLastAssistantContent,
        updateLastAssistantSources,
        updateLastAssistantQueryRewrite,
      }}
    >
      {children}
    </ChatContext.Provider>
  );
}
