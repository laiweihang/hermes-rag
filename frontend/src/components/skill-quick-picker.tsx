"use client";

import Link from "next/link";
import { Sparkles, Check, Settings2 } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { useChatContext } from "@/lib/chat-context";

export function SkillQuickPicker() {
  const { skills, activeSkillId, setActiveSkillId, isAdmin } = useChatContext();

  const current = skills.find((s) => s.id === activeSkillId);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button variant="ghost" size="sm" className="h-8 gap-1.5 text-xs">
            <span className="text-base leading-none">
              {current?.icon || "🧰"}
            </span>
            <span className="text-muted-foreground">技能:</span>
            <span className="font-medium truncate max-w-[120px]">
              {current?.name ?? "通用助手"}
            </span>
          </Button>
        }
      />
      <DropdownMenuContent align="end" className="w-64">
        <div className="flex items-center gap-2 px-1.5 py-1 text-xs font-medium text-muted-foreground">
          <Sparkles className="size-4 text-primary" />
          切换技能场景
        </div>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onClick={() => setActiveSkillId(null)}
          className="flex items-start gap-2"
        >
          <span className="text-base leading-none">🧰</span>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium">通用助手</p>
            <p className="text-[11px] text-muted-foreground">不附加任何技能提示词</p>
          </div>
          {activeSkillId === null && <Check className="size-3.5 mt-1 text-primary" />}
        </DropdownMenuItem>
        {skills.length > 0 && <DropdownMenuSeparator />}
        {skills.length === 0 ? (
          <div className="px-2 py-3 text-center text-xs text-muted-foreground">
            尚未配置技能
          </div>
        ) : (
          skills.map((s) => (
            <DropdownMenuItem
              key={s.id}
              onClick={() =>
                setActiveSkillId(activeSkillId === s.id ? null : s.id)
              }
              className="flex items-start gap-2"
            >
              <span className="text-base leading-none">{s.icon || "🔧"}</span>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate">{s.name}</p>
                <p className="text-[11px] text-muted-foreground line-clamp-2">
                  {s.description}
                </p>
              </div>
              {activeSkillId === s.id && (
                <Check className="size-3.5 mt-1 text-primary shrink-0" />
              )}
            </DropdownMenuItem>
          ))
        )}
        {isAdmin && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              render={
                <Link href="/admin" className="text-xs text-muted-foreground">
                  <Settings2 className="size-3.5 mr-1.5" />
                  管理后台 · 技能管理
                </Link>
              }
            />
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
