"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { Cloud, Check, AlertCircle, Settings2 } from "lucide-react";
import { useChatContext, type LlmProvider } from "@/lib/chat-context";
import { ScrollArea } from "@/components/ui/scroll-area";

function ModelCard({
  provider,
  isActive,
  onSelect,
}: {
  provider: LlmProvider;
  isActive: boolean;
  onSelect: () => void;
}) {
  return (
    <motion.div
      layout
      whileHover={{
        scale: 1.03,
        boxShadow: "0 4px 20px rgba(0,0,0,0.12)",
      }}
      whileTap={{ scale: 0.98 }}
      transition={{ type: "spring", stiffness: 400, damping: 25 }}
      onClick={onSelect}
      className={`relative cursor-pointer rounded-xl border p-3 ${
        isActive
          ? "border-primary bg-primary/5 shadow-md"
          : "border-border bg-card hover:border-primary/40"
      }`}
    >
      <div className="flex items-start gap-3">
        <div className="shrink-0 rounded-lg p-1.5 bg-emerald-500/10">
          <Cloud className="size-4 text-emerald-600" />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <p className="text-sm font-medium truncate text-foreground">
              {provider.name}
            </p>
            {isActive && (
              <motion.span
                initial={{ opacity: 0, scale: 0 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ type: "spring", stiffness: 500, damping: 25 }}
              >
                <Check className="size-3.5 text-primary shrink-0" />
              </motion.span>
            )}
          </div>
          <p className="text-xs text-muted-foreground truncate mt-0.5">
            {provider.model_name}
          </p>
        </div>

        {provider.is_default && (
          <span className="shrink-0 rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
            默认
          </span>
        )}
      </div>

      {isActive && (
        <motion.div
          layoutId="model-active-bar"
          className="absolute left-0 top-2 bottom-2 w-[3px] rounded-full bg-primary"
          transition={{ type: "spring", stiffness: 500, damping: 30 }}
        />
      )}
    </motion.div>
  );
}

export function ModelSelector() {
  const { providers, activeProviderId, setActiveProviderId, isAdmin } = useChatContext();

  if (providers.length === 0) {
    return (
      <div className="p-3 space-y-2">
        <div className="flex items-center justify-center gap-1.5 text-xs text-muted-foreground">
          <AlertCircle className="size-3.5 text-amber-500" />
          暂无可用 LLM 模型
        </div>
        {isAdmin ? (
          <Link
            href="/admin"
            className="flex h-8 w-full items-center justify-center gap-1 rounded-md border border-border bg-background px-2.5 text-xs font-medium hover:bg-muted hover:text-foreground"
          >
            <Settings2 className="size-3.5" />
            去模型管理配置 API Key
          </Link>
        ) : (
          <p className="text-[11px] text-center text-muted-foreground">
            请联系管理员配置 LLM Provider
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="p-2">
      <p className="px-2 py-1 text-xs font-medium text-muted-foreground">
        对话模型
      </p>
      <ScrollArea className="max-h-[200px]">
        <div className="flex flex-col gap-1.5 py-1">
          {providers.map((provider) => (
            <ModelCard
              key={provider.id}
              provider={provider}
              isActive={activeProviderId === provider.id}
              onSelect={() =>
                setActiveProviderId(
                  activeProviderId === provider.id ? null : provider.id
                )
              }
            />
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}
