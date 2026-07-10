"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { useRouter } from "next/navigation";
import { getToken } from "@/lib/api";
import { ChatProvider } from "@/lib/chat-context";
import { Sidebar } from "@/components/layout/sidebar";
import { TopBar } from "@/components/layout/top-bar";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { motion } from "framer-motion";

export default function MainLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const mounted = useSyncExternalStore(() => () => {}, () => true, () => false);
  const token = mounted ? getToken() : null;
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    if (mounted && !token) {
      router.replace("/login");
    }
  }, [mounted, router, token]);

  if (!mounted || !token) {
    return null;
  }

  return (
    <ChatProvider>
      <div className="flex h-screen overflow-hidden bg-background">
        {/* Desktop sidebar */}
        <motion.aside
          className="hidden md:flex flex-col border-r border-border bg-sidebar overflow-hidden"
          animate={{ width: sidebarCollapsed ? 0 : 280 }}
          transition={{ type: "spring", stiffness: 300, damping: 30 }}
        >
          <Sidebar
            collapsed={sidebarCollapsed}
            onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
          />
        </motion.aside>

        {/* Mobile sidebar (Sheet) */}
        <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
          <SheetContent side="left" className="w-[280px] p-0" showCloseButton={false}>
            <Sidebar
              collapsed={false}
              onToggle={() => setMobileOpen(false)}
            />
          </SheetContent>
        </Sheet>

        {/* Main content area */}
        <div className="flex flex-1 flex-col overflow-hidden">
          <TopBar
            sidebarCollapsed={sidebarCollapsed}
            onToggleSidebar={() => setSidebarCollapsed(!sidebarCollapsed)}
            onMobileMenuOpen={() => setMobileOpen(true)}
          />
          <main className="flex-1 overflow-auto">{children}</main>
        </div>
      </div>
    </ChatProvider>
  );
}
