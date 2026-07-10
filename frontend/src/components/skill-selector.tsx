"use client";

import { motion } from "framer-motion";
import { Sparkles } from "lucide-react";
import { useChatContext, type Skill } from "@/lib/chat-context";
import { ScrollArea } from "@/components/ui/scroll-area";

/* ------------------------------------------------------------------ */
/*  Skill card                                                         */
/* ------------------------------------------------------------------ */

function SkillCard({
  skill,
  isActive,
  onSelect,
}: {
  skill: Skill;
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
        {/* Icon */}
        <span className="text-2xl leading-none shrink-0">{skill.icon || "🔧"}</span>

        {/* Text content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <p className="text-sm font-medium truncate text-foreground">
              {skill.name}
            </p>
            {isActive && (
              <motion.span
                initial={{ opacity: 0, scale: 0 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ type: "spring", stiffness: 500, damping: 25 }}
              >
                <Sparkles className="size-3.5 text-primary shrink-0" />
              </motion.span>
            )}
          </div>
          <p className="text-xs text-muted-foreground line-clamp-2 mt-0.5">
            {skill.description}
          </p>
        </div>
      </div>

      {/* Active indicator bar */}
      {isActive && (
        <motion.div
          layoutId="skill-active-bar"
          className="absolute left-0 top-2 bottom-2 w-[3px] rounded-full bg-primary"
          transition={{ type: "spring", stiffness: 500, damping: 30 }}
        />
      )}
    </motion.div>
  );
}

/* ------------------------------------------------------------------ */
/*  Skill selector                                                     */
/* ------------------------------------------------------------------ */

export function SkillSelector() {
  const { skills, activeSkillId, setActiveSkillId } = useChatContext();

  if (skills.length === 0) {
    return (
      <div className="px-2 py-3 text-center text-xs text-muted-foreground">
        暂无可用技能
      </div>
    );
  }

  return (
    <div className="p-2">
      <p className="px-2 py-1 text-xs font-medium text-muted-foreground">
        技能场景
      </p>
      <ScrollArea className="max-h-[240px]">
        <div className="flex flex-col gap-1.5 py-1">
          {skills.map((skill) => (
            <SkillCard
              key={skill.id}
              skill={skill}
              isActive={activeSkillId === skill.id}
              onSelect={() =>
                setActiveSkillId(activeSkillId === skill.id ? null : skill.id)
              }
            />
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}
