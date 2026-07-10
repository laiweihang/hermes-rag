"use client";

import Link from "next/link";
import { Bot, Hash, ScanText, Settings2, Check, AlertCircle } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { useChatContext, type LlmProvider, type ModelType } from "@/lib/chat-context";

const META: Record<
  ModelType,
  { label: string; icon: typeof Bot; tone: string }
> = {
  llm: {
    label: "对话模型",
    icon: Bot,
    tone: "text-blue-600 dark:text-blue-400",
  },
  embedding: {
    label: "嵌入模型",
    icon: Hash,
    tone: "text-emerald-600 dark:text-emerald-400",
  },
  ocr: {
    label: "OCR 模型",
    icon: ScanText,
    tone: "text-amber-600 dark:text-amber-400",
  },
};

interface ProviderQuickPickerProps {
  /** 类型：llm / embedding / ocr */
  type: ModelType;
  /**
   * "default" 模式：仅修改默认 Provider（管理员可点击切换；普通用户只读）。
   * "session" 模式：仅修改当前会话内选择的 Provider，不写入数据库。
   */
  mode?: "default" | "session";
  /** session 模式下，当前选中的 Provider id */
  sessionValue?: number | null;
  /** session 模式下，切换回调 */
  onSessionChange?: (id: number | null) => void;
  /** 紧凑模式：仅图标 + 短名 */
  compact?: boolean;
}

export function ProviderQuickPicker({
  type,
  mode = "default",
  sessionValue,
  onSessionChange,
  compact = false,
}: ProviderQuickPickerProps) {
  const ctx = useChatContext();
  const { isAdmin, setDefaultProvider } = ctx;

  const list: LlmProvider[] =
    type === "llm" ? ctx.providers
    : type === "embedding" ? ctx.embeddingProviders
    : ctx.ocrProviders;

  const meta = META[type];
  const Icon = meta.icon;

  // 当前展示选中
  let current: LlmProvider | undefined;
  if (mode === "session") {
    current =
      list.find((p) => p.id === sessionValue) ??
      list.find((p) => p.is_default) ??
      list[0];
  } else {
    current = list.find((p) => p.is_default) ?? list[0];
  }

  // 是否允许点击修改
  const canChange = mode === "session" || isAdmin;

  if (list.length === 0) {
    return (
      <Link
        href="/admin"
        className="flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
      >
        <AlertCircle className="size-3.5 text-amber-500" />
        {meta.label}未配置
        <Settings2 className="size-3" />
      </Link>
    );
  }

  const triggerContent = (
    <>
      <Icon className={`size-3.5 ${meta.tone}`} />
      {!compact && <span className="text-muted-foreground">{meta.label}:</span>}
      <span className="font-medium truncate max-w-[140px]">
        {current?.name ?? "未选择"}
      </span>
    </>
  );

  // 不能修改 + 单一项 → 静态展示
  if (!canChange && list.length <= 1) {
    return (
      <Button
        variant="ghost"
        size="sm"
        className="h-8 gap-1.5 text-xs"
        disabled
      >
        {triggerContent}
      </Button>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button variant="ghost" size="sm" className="h-8 gap-1.5 text-xs">
            {triggerContent}
          </Button>
        }
      />
      <DropdownMenuContent align="end" className="w-64">
        <div className="flex items-center gap-2 px-1.5 py-1 text-xs font-medium text-muted-foreground">
          <Icon className={`size-4 ${meta.tone}`} />
          切换{meta.label}
          {mode === "default" && !isAdmin && (
            <span className="ml-auto text-[10px]">仅管理员可切换</span>
          )}
        </div>
        <DropdownMenuSeparator />
        {list.map((p) => {
          const selected =
            mode === "session"
              ? (sessionValue ?? null) === p.id
              : p.is_default;
          return (
            <DropdownMenuItem
              key={p.id}
              disabled={!canChange}
              onClick={async () => {
                if (!canChange) return;
                if (mode === "session") {
                  onSessionChange?.(selected ? null : p.id);
                } else {
                  if (!p.is_default) {
                    await setDefaultProvider(p.id);
                  }
                }
              }}
              className="flex items-start gap-2"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-sm font-medium truncate">{p.name}</span>
                  {p.is_default && mode === "default" && (
                    <span className="text-[10px] rounded bg-primary/10 text-primary px-1.5 py-0.5">
                      默认
                    </span>
                  )}
                </div>
                <p className="text-[11px] text-muted-foreground truncate">
                  {p.model_name}
                </p>
              </div>
              {selected && <Check className="size-3.5 mt-1 text-primary shrink-0" />}
            </DropdownMenuItem>
          );
        })}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          render={
            <Link href="/admin" className="text-xs text-muted-foreground">
              <Settings2 className="size-3.5 mr-1.5" />
              管理后台 · 模型管理
            </Link>
          }
        />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
