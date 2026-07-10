"use client";

import { motion } from "framer-motion";

const dotVariants = {
  initial: { y: 0, opacity: 0.4 },
  animate: { y: -6, opacity: 1 },
};

const dotTransition = (delay: number) => ({
  type: "spring" as const,
  stiffness: 300,
  damping: 15,
  repeat: Infinity,
  repeatType: "reverse" as const,
  delay,
});

export default function Thinking() {
  return (
    <div className="flex items-center gap-2 px-4 py-3">
      <span className="text-sm text-muted-foreground font-medium">Thinking</span>
      <div className="flex items-center gap-1">
        {[0, 1, 2].map((i) => (
          <motion.span
            key={i}
            className="block h-1.5 w-1.5 rounded-full bg-primary"
            variants={dotVariants}
            initial="initial"
            animate="animate"
            transition={dotTransition(i * 0.15)}
          />
        ))}
      </div>
    </div>
  );
}
