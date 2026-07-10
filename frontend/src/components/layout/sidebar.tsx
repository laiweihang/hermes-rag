"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  Plus,
  MessageSquare,
  Trash2,
  PanelLeftClose,
  FileText,
  Database,
  Shield,
} from "lucide-react";
import { useChatContext } from "@/lib/chat-context";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { SkillSelector } from "@/components/skill-selector";
import { ModelSelector } from "@/components/model-selector";

const NAV_ITEMS = [
  { href: "/", label: "对话", icon: MessageSquare },
] as const;

const ADMIN_NAV_ITEMS = [
  { href: "/documents", label: "文档管理", icon: FileText },
  { href: "/vectors", label: "向量片段", icon: Database },
] as const;

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

export function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const pathname = usePathname();
  const {
    conversations,
    activeConversationId,
    setActiveConversationId,
    createConversation,
    deleteConversation,
    isAdmin,
  } = useChatContext();

  if (collapsed) return null;

  const isOnChat = pathname === "/";

  return (
    <div className="flex h-full flex-col">
      {/* Navigation links */}
      <nav className="p-2 space-y-0.5">
        {NAV_ITEMS.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors ${
                active
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-sidebar-foreground hover:bg-sidebar-accent/50"
              }`}
            >
              <item.icon className="size-4 shrink-0" />
              {item.label}
            </Link>
          );
        })}
        {isAdmin && ADMIN_NAV_ITEMS.map((item) => {
          const active = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors ${
                active
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-sidebar-foreground hover:bg-sidebar-accent/50"
              }`}
            >
              <item.icon className="size-4 shrink-0" />
              {item.label}
            </Link>
          );
        })}
        {isAdmin && (
          <Link
            href="/admin"
            className={`flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors ${
              pathname.startsWith("/admin")
                ? "bg-sidebar-accent text-sidebar-accent-foreground"
                : "text-sidebar-foreground hover:bg-sidebar-accent/50"
            }`}
          >
            <Shield className="size-4 shrink-0" />
            管理后台
          </Link>
        )}
      </nav>

      <Separator />

      {/* Conversation section — only when on chat page */}
      {isOnChat && (
        <>
          <div className="flex items-center justify-between p-3">
            <Button
              variant="outline"
              size="sm"
              className="flex-1 justify-start gap-2"
              onClick={() => createConversation()}
            >
              <Plus className="size-4" />
              新建对话
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="ml-2 hidden md:flex"
              onClick={onToggle}
              aria-label="收起侧边栏"
            >
              <PanelLeftClose className="size-4" />
            </Button>
          </div>

          <Separator />

          <div className="flex-1 overflow-hidden">
            <ScrollArea className="h-full">
              <div className="p-2">
                <p className="px-2 py-1 text-xs font-medium text-muted-foreground">
                  对话列表
                </p>
                {conversations.length === 0 ? (
                  <p className="px-2 py-4 text-center text-sm text-muted-foreground">
                    暂无对话
                  </p>
                ) : (
                  <div className="flex flex-col gap-0.5">
                    {conversations.map((conv) => (
                      <motion.div
                        key={conv.id}
                        initial={{ opacity: 0, x: -8 }}
                        animate={{ opacity: 1, x: 0 }}
                        transition={{ type: "spring", stiffness: 300, damping: 25 }}
                      >
                        <div
                          role="button"
                          tabIndex={0}
                          className={`group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-sidebar-accent cursor-pointer ${
                            activeConversationId === conv.id
                              ? "bg-sidebar-accent text-sidebar-accent-foreground"
                              : "text-sidebar-foreground"
                          }`}
                          onClick={() => setActiveConversationId(conv.id)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              setActiveConversationId(conv.id);
                            }
                          }}
                        >
                          <MessageSquare className="size-4 shrink-0" />
                          <span className="flex-1 truncate">
                            {conv.title || "新对话"}
                          </span>
                          <button
                            className="opacity-0 group-hover:opacity-100 transition-opacity"
                            onClick={(e) => {
                              e.stopPropagation();
                              deleteConversation(conv.id);
                            }}
                            aria-label="删除对话"
                          >
                            <Trash2 className="size-3.5 text-muted-foreground hover:text-destructive" />
                          </button>
                        </div>
                      </motion.div>
                    ))}
                  </div>
                )}
              </div>
            </ScrollArea>
          </div>

          <Separator />
          <ModelSelector />
          <Separator />
          <SkillSelector />
        </>
      )}

      {/* Spacer when not on chat page */}
      {!isOnChat && (
        <>
          <div className="flex-1" />
          <Separator />
          <div className="flex items-center justify-between p-3">
            <Button
              variant="ghost"
              size="icon"
              className="ml-auto hidden md:flex"
              onClick={onToggle}
              aria-label="收起侧边栏"
            >
              <PanelLeftClose className="size-4" />
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
