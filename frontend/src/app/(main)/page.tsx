"use client";

import { useRef, useEffect, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Send,
  MessageSquare,
  ThumbsUp,
  ThumbsDown,
  ChevronDown,
  ChevronUp,
  Download,
  FileText,
  BookMarked,
  Sparkles,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useChatContext, type Message } from "@/lib/chat-context";
import { getToken, post, get as apiGet } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import Thinking from "@/components/thinking";
import { ProviderQuickPicker } from "@/components/provider-quick-picker";
import { SkillQuickPicker } from "@/components/skill-quick-picker";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/* ------------------------------------------------------------------ */
/*  Query rewrite display                                              */
/* ------------------------------------------------------------------ */

interface QueryRewriteInfo {
  original: string;
  simple: string | null;
  hyde: string | null;
  bm25_query: string;
  vector_query: string;
  simple_enabled: boolean;
  hyde_enabled: boolean;
}

/**
 * 展示后端 LLM 增强后的查询：原始查询 + 简单重写 + HyDE 假设性答案。
 * 默认折叠，点击展开看完整内容；让用户对 LLM 究竟把查询改成了什么有
 * 透明度——RAG 黑盒里最常被怀疑的环节就是检索查询。
 */
function QueryRewritePanel({ rewriteJson }: { rewriteJson: string }) {
  const [expanded, setExpanded] = useState(false);

  let info: QueryRewriteInfo | null = null;
  try {
    info = JSON.parse(rewriteJson) as QueryRewriteInfo;
  } catch {
    return null;
  }
  if (!info) return null;

  // 没产生任何重写结果时不显示——避免"开关开了但生成失败"也占位面板。
  const hasAny = (info.simple && info.simple.trim()) || (info.hyde && info.hyde.trim());
  if (!hasAny) return null;

  // 折叠态展示一行最关键的信息：用 simple 优先；都有时显示「简单重写 + HyDE」。
  const summaryLine = info.simple
    ? info.simple
    : info.hyde
      ? info.hyde.slice(0, 60) + (info.hyde.length > 60 ? "…" : "")
      : "";

  return (
    <div className="mb-2">
      <button
        className="flex w-full items-center gap-1.5 rounded-md border border-violet-500/30 bg-violet-500/5 px-2.5 py-1.5 text-xs text-violet-700 dark:text-violet-300 hover:bg-violet-500/10 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <Sparkles className="size-3.5 shrink-0" />
        <span className="font-medium shrink-0">LLM 已优化检索查询</span>
        <span className="truncate text-violet-600/80 dark:text-violet-400/80 flex-1 text-left">
          {summaryLine}
        </span>
        {expanded ? <ChevronUp className="size-3 shrink-0" /> : <ChevronDown className="size-3 shrink-0" />}
      </button>
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ type: "spring", stiffness: 300, damping: 30 }}
            className="overflow-hidden"
          >
            <div className="mt-2 space-y-2 rounded-md border border-violet-500/20 bg-violet-500/5 p-2.5 text-xs">
              <div>
                <div className="mb-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  原始查询
                </div>
                <p className="whitespace-pre-wrap text-foreground/90">{info.original}</p>
              </div>

              {info.simple && (
                <div>
                  <div className="mb-0.5 flex items-center gap-1.5">
                    <Badge variant="outline" className="text-[10px] px-1.5 py-0 border-violet-500/40">
                      简单重写
                    </Badge>
                    <span className="text-[10px] text-muted-foreground">→ BM25 / 向量通道</span>
                  </div>
                  <p className="whitespace-pre-wrap text-foreground/90">{info.simple}</p>
                </div>
              )}

              {info.hyde && (
                <div>
                  <div className="mb-0.5 flex items-center gap-1.5">
                    <Badge variant="outline" className="text-[10px] px-1.5 py-0 border-violet-500/40">
                      HyDE 假设性答案
                    </Badge>
                    <span className="text-[10px] text-muted-foreground">→ 向量通道</span>
                  </div>
                  <p className="whitespace-pre-wrap text-foreground/90 leading-relaxed">{info.hyde}</p>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Source display                                                     */
/* ------------------------------------------------------------------ */

interface SourceItem {
  content: string;
  source: string;
  score?: number;
}

function SourcesPanel({ sourcesJson }: { sourcesJson: string }) {
  const [expanded, setExpanded] = useState(false);

  let sources: SourceItem[] = [];
  try {
    const parsed: unknown = JSON.parse(sourcesJson);
    if (Array.isArray(parsed)) {
      sources = parsed as SourceItem[];
    }
  } catch {
    return null;
  }

  if (sources.length === 0) return null;

  return (
    <div className="mt-2">
      <button
        className="flex items-center gap-1 text-xs text-primary hover:underline"
        onClick={() => setExpanded(!expanded)}
      >
        <FileText className="size-3" />
        {sources.length} 个参考来源
        {expanded ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />}
      </button>
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ type: "spring", stiffness: 300, damping: 30 }}
            className="overflow-hidden"
          >
            <div className="mt-2 space-y-2">
              {sources.map((src, idx) => (
                <div
                  key={idx}
                  className="rounded-md border border-border/60 bg-muted/30 p-2 text-xs"
                >
                  <div className="flex items-center gap-2 mb-1">
                    <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                      {src.source}
                    </Badge>
                    {src.score != null && (
                      <span className="text-muted-foreground">
                        相似度: {(1 - src.score).toFixed(2)}
                      </span>
                    )}
                  </div>
                  <p className="text-muted-foreground line-clamp-3 whitespace-pre-wrap">
                    {src.content}
                  </p>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Feedback buttons                                                   */
/* ------------------------------------------------------------------ */

function FeedbackButtons({ messageId }: { messageId: number }) {
  const [voted, setVoted] = useState<"up" | "down" | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleVote(rating: "up" | "down") {
    if (submitting || voted) return;
    setSubmitting(true);
    try {
      await post("/api/feedback", { message_id: messageId, rating });
      setVoted(rating);
    } catch {
      /* silent */
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex gap-1 mt-1">
      <button
        className={`p-1 rounded transition-colors ${
          voted === "up"
            ? "text-green-600"
            : "text-muted-foreground hover:text-green-600"
        }`}
        onClick={() => handleVote("up")}
        disabled={voted !== null || submitting}
        aria-label="赞"
      >
        <ThumbsUp className="size-3.5" />
      </button>
      <button
        className={`p-1 rounded transition-colors ${
          voted === "down"
            ? "text-red-500"
            : "text-muted-foreground hover:text-red-500"
        }`}
        onClick={() => handleVote("down")}
        disabled={voted !== null || submitting}
        aria-label="踩"
      >
        <ThumbsDown className="size-3.5" />
      </button>
    </div>
  );
}

function RagReferenceToggle({ className }: { className?: string }) {
  const { useRag, setUseRag, documentCount } = useChatContext();

  return (
    <button
      type="button"
      role="switch"
      aria-checked={useRag}
      aria-label="是否参考知识库文档进行检索"
      onClick={() => setUseRag(!useRag)}
      className={cn(
        "inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-xs transition-colors select-none",
        useRag
          ? "border-primary/40 bg-primary/10 text-foreground"
          : "border-border bg-muted/40 text-muted-foreground opacity-90",
        className,
      )}
    >
      <BookMarked className="size-3.5 shrink-0" />
      <span className="font-medium">参考文档</span>
      <Badge
        variant="secondary"
        className={cn(
          "h-5 px-1.5 text-[10px] font-normal tabular-nums",
          documentCount === 0 && "opacity-70",
        )}
      >
        {documentCount} 篇
      </Badge>
    </button>
  );
}

const ASSISTANT_MARKDOWN_CLASS =
  "markdown-chat text-left break-words [&_p]:mb-2 [&_p:last-child]:mb-0 [&_ul]:my-2 [&_ul]:ml-4 [&_ul]:list-disc [&_ol]:my-2 [&_ol]:ml-4 [&_ol]:list-decimal [&_li]:my-0.5 [&_h1]:mb-2 [&_h1]:mt-3 [&_h1]:text-lg [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:mt-2 [&_h2]:text-base [&_h2]:font-semibold [&_h3]:mb-1 [&_h3]:mt-2 [&_h3]:text-sm [&_h3]:font-medium [&_strong]:font-semibold [&_a]:text-primary [&_a]:underline [&_a]:underline-offset-2 [&_code]:rounded [&_code]:bg-background/60 [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-[0.9em] [&_pre]:my-2 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:border [&_pre]:border-border/60 [&_pre]:bg-background/50 [&_pre]:p-3 [&_pre]:text-xs [&_blockquote]:border-l-2 [&_blockquote]:border-muted-foreground/40 [&_blockquote]:pl-3 [&_blockquote]:italic [&_table]:my-2 [&_table]:w-full [&_table]:border-collapse [&_table]:text-xs [&_th]:border [&_th]:border-border [&_th]:bg-muted/50 [&_th]:px-2 [&_th]:py-1 [&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1 [&_hr]:my-3 [&_hr]:border-border";

/* ------------------------------------------------------------------ */
/*  Message bubble                                                     */
/* ------------------------------------------------------------------ */

function MessageBubble({ msg, isStreaming }: { msg: Message; isStreaming?: boolean }) {
  const isUser = msg.role === "user";
  const isRealMessage = msg.id > 1000;

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: "spring", stiffness: 300, damping: 30 }}
      className={`flex ${isUser ? "justify-end" : "justify-start"}`}
    >
      <div className={`max-w-[80%] ${isUser ? "" : ""}`}>
        {/* Query rewrite preview — 放在助手消息气泡上方，让用户在内容生成
            过程中就能看到 LLM 增强后的检索查询 */}
        {!isUser && msg.query_rewrite && (
          <QueryRewritePanel rewriteJson={msg.query_rewrite} />
        )}

        <div
          className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
            isUser
              ? "bg-primary text-primary-foreground whitespace-pre-wrap"
              : "bg-muted text-foreground"
          }`}
        >
          {isUser ? (
            msg.content
          ) : (
            <div className={ASSISTANT_MARKDOWN_CLASS}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
            </div>
          )}
          {msg.rule_matched && (
            <Badge variant="outline" className="ml-2 text-[10px] align-middle">
              规则匹配: {msg.rule_matched}
            </Badge>
          )}
        </div>

        {/* Sources */}
        {!isUser && msg.sources && <SourcesPanel sourcesJson={msg.sources} />}

        {/* Feedback — only for real assistant messages that are done streaming */}
        {!isUser && isRealMessage && !isStreaming && msg.content && (
          <FeedbackButtons messageId={msg.id} />
        )}
      </div>
    </motion.div>
  );
}

/* ------------------------------------------------------------------ */
/*  Empty state                                                        */
/* ------------------------------------------------------------------ */

function EmptyState({ onNewConversation }: { onNewConversation: () => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ type: "spring", stiffness: 260, damping: 24 }}
        className="flex flex-col items-center gap-3 text-center"
      >
        <div className="rounded-full bg-muted p-4">
          <MessageSquare className="size-8 text-muted-foreground" />
        </div>
        <h2 className="text-xl font-semibold text-foreground">
          赫尔墨斯 Hermes
        </h2>
        <p className="text-sm text-muted-foreground max-w-sm">
          选择一个对话或创建新对话开始。把文档变成答案，把知识变成行动。
        </p>
        <Button onClick={onNewConversation} className="mt-2 gap-2">
          <MessageSquare className="size-4" />
          新建对话
        </Button>
      </motion.div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Export helper                                                       */
/* ------------------------------------------------------------------ */

async function handleExport(conversationId: number, format: "json" | "csv") {
  try {
    const data = await apiGet<Blob>(
      `/api/export/conversations/${conversationId}?format=${format}`,
      { responseType: "blob" as never }
    );
    const blob = data instanceof Blob ? data : new Blob([JSON.stringify(data)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `conversation-${conversationId}.${format}`;
    a.click();
    URL.revokeObjectURL(url);
  } catch {
    /* silent */
  }
}

/* ------------------------------------------------------------------ */
/*  Chat page                                                          */
/* ------------------------------------------------------------------ */

export default function ChatPage() {
  const {
    activeConversationId,
    messages,
    loadingMessages,
    createConversation,
    refreshConversations,
    updateConversationTitle,
    activeSkillId,
    activeProviderId,
    setActiveProviderId,
    isStreaming,
    setIsStreaming,
    appendMessage,
    updateLastAssistantContent,
    updateLastAssistantSources,
    updateLastAssistantQueryRewrite,
    useRag,
    documentCount,
  } = useChatContext();

  const [input, setInput] = useState("");
  const [isThinking, setIsThinking] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  /* auto-scroll to bottom on new messages */
  useEffect(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages, isThinking]);

  /* focus textarea when conversation changes */
  useEffect(() => {
    if (activeConversationId) {
      textareaRef.current?.focus();
    }
  }, [activeConversationId]);

  /* ---- send message with SSE streaming ---- */
  const handleSend = useCallback(async () => {
    const question = input.trim();
    if (!question || isStreaming) return;

    let convId = activeConversationId;

    if (!convId) {
      const conv = await createConversation();
      if (!conv) return;
      convId = conv.id;
    }

    setInput("");
    setIsStreaming(true);
    setIsThinking(true);

    const userMsg: Message = {
      id: Date.now(),
      role: "user",
      content: question,
      created_at: new Date().toISOString(),
    };
    appendMessage(userMsg);

    const assistantMsg: Message = {
      id: Date.now() + 1,
      role: "assistant",
      content: "",
      created_at: new Date().toISOString(),
    };
    appendMessage(assistantMsg);

    let accumulated = "";

    try {
      const token = getToken();
      const res = await fetch(
        `${API_BASE}/api/conversations/${convId}/messages/stream`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({
            question,
            use_rag: useRag,
            ...(activeSkillId ? { skill_id: activeSkillId } : {}),
            ...(activeProviderId ? { provider_id: activeProviderId } : {}),
          }),
        }
      );

      if (!res.ok || !res.body) {
        const errText = await res.text().catch(() => "");
        updateLastAssistantContent(
          `⚠️ 请求失败 (HTTP ${res.status})${errText ? "：" + errText.slice(0, 200) : "，请检查模型 Provider 是否已配置 API Key。"}`
        );
        setIsThinking(false);
        setIsStreaming(false);
        // 用户消息已在后端入库，刷新对话列表避免「列表丢失」错觉
        refreshConversations();
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith("data: ")) continue;

          const payload = trimmed.slice(6);
          if (payload === "[DONE]") continue;
          if (payload.startsWith("[REWRITE]")) {
            // 后端 generate_answer_stream 在 token 流之前先发出这个事件，
            // 让用户在思考动画结束前就能看到 LLM 优化后的检索查询。
            updateLastAssistantQueryRewrite(payload.slice(9));
            continue;
          }
          if (payload.startsWith("[SOURCES]")) {
            updateLastAssistantSources(payload.slice(9));
            continue;
          }
          if (payload.startsWith("[ERROR]")) {
            accumulated += "\n⚠️ " + payload;
            updateLastAssistantContent(accumulated);
            continue;
          }

          if (accumulated === "") {
            setIsThinking(false);
          }

          accumulated += payload;
          updateLastAssistantContent(accumulated);
        }
      }

      if (convId) {
        const autoTitle =
          question.length > 30 ? question.slice(0, 30) + "..." : question;
        updateConversationTitle(convId, autoTitle);
        refreshConversations();
      }
    } catch {
      updateLastAssistantContent(
        accumulated.length > 0
          ? accumulated + "\n\n⚠️ 连接中断"
          : "网络错误，请检查连接后重试。"
      );
    } finally {
      setIsThinking(false);
      setIsStreaming(false);
    }
  }, [
    input,
    isStreaming,
    activeConversationId,
    activeSkillId,
    activeProviderId,
    createConversation,
    appendMessage,
    updateLastAssistantContent,
    updateLastAssistantSources,
    updateLastAssistantQueryRewrite,
    updateConversationTitle,
    refreshConversations,
    setIsStreaming,
    useRag,
  ]);

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  if (!activeConversationId && messages.length === 0) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex items-center gap-1 border-b border-border px-3 py-1.5 flex-wrap">
          <ProviderQuickPicker
            type="llm"
            mode="session"
            sessionValue={activeProviderId}
            onSessionChange={setActiveProviderId}
          />
          <span className="h-4 w-px bg-border mx-1" />
          <SkillQuickPicker />
          <span className="h-4 w-px bg-border mx-1 hidden sm:block" />
          <RagReferenceToggle />
        </div>
        <div className="flex-1">
          <EmptyState onNewConversation={() => createConversation()} />
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* 顶部条：模型 + 技能 + 导出 */}
      <div className="flex items-center gap-1 border-b border-border px-3 py-1.5 flex-wrap">
        <ProviderQuickPicker
          type="llm"
          mode="session"
          sessionValue={activeProviderId}
          onSessionChange={setActiveProviderId}
        />
        <span className="h-4 w-px bg-border mx-1" />
        <SkillQuickPicker />
        <span className="h-4 w-px bg-border mx-1 hidden sm:block" />
        <RagReferenceToggle className="hidden sm:inline-flex" />
        <div className="ml-auto flex items-center gap-1">
          {activeConversationId && (
            <>
              <Button
                variant="ghost"
                size="sm"
                className="text-xs"
                onClick={() => handleExport(activeConversationId, "json")}
              >
                <Download className="size-3 mr-1" />JSON
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="text-xs"
                onClick={() => handleExport(activeConversationId, "csv")}
              >
                <Download className="size-3 mr-1" />CSV
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Message list */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <ScrollArea className="h-full">
          <div className="mx-auto max-w-3xl space-y-4 px-4 py-6">
            {loadingMessages ? (
              <div className="flex justify-center py-8">
                <Thinking />
              </div>
            ) : messages.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                发送消息开始对话
              </p>
            ) : (
              messages.map((msg) => (
                <MessageBubble
                  key={msg.id}
                  msg={msg}
                  isStreaming={isStreaming && msg.id === messages[messages.length - 1]?.id && msg.role === "assistant"}
                />
              ))
            )}

            {isThinking && (
              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ type: "spring", stiffness: 300, damping: 25 }}
                className="flex justify-start"
              >
                <div className="rounded-2xl bg-muted px-4 py-2.5">
                  <Thinking />
                </div>
              </motion.div>
            )}
          </div>
        </ScrollArea>
      </div>

      {/* Chat input */}
      <div className="border-t border-border bg-background p-4">
        <div className="mx-auto mb-2 flex max-w-3xl flex-wrap items-center gap-2 text-xs">
          <RagReferenceToggle className="sm:hidden" />
          {!useRag && (
            <span className="text-muted-foreground">本消息将不检索知识库</span>
          )}
          {useRag && documentCount === 0 && (
            <span className="text-muted-foreground">知识库暂无文档，将按通识回答</span>
          )}
        </div>
        <div className="mx-auto flex max-w-3xl items-end gap-2">
          <Textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
            className="min-h-[44px] max-h-[160px] resize-none"
            rows={1}
            disabled={isStreaming}
          />
          <Button
            size="icon"
            className="size-11 shrink-0"
            onClick={handleSend}
            disabled={isStreaming || !input.trim()}
            aria-label="发送消息"
          >
            <Send className="size-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
