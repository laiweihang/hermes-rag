"use client";

import { useSyncExternalStore } from "react";
import { useRouter } from "next/navigation";
import { Menu, PanelLeft, LogOut, User, Sun, Moon } from "lucide-react";
import { useTheme } from "next-themes";
import { getToken, removeToken } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuLabel,
} from "@/components/ui/dropdown-menu";

interface TopBarProps {
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
  onMobileMenuOpen: () => void;
}

function parseJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const payload = parts[1];
    const decoded = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(decoded);
  } catch {
    return null;
  }
}

export function TopBar({
  sidebarCollapsed,
  onToggleSidebar,
  onMobileMenuOpen,
}: TopBarProps) {
  const router = useRouter();
  const { theme, setTheme } = useTheme();
  const mounted = useSyncExternalStore(() => () => {}, () => true, () => false);
  const payload = mounted && getToken() ? parseJwtPayload(getToken()!) : null;
  const username = payload && typeof payload.sub === "string" ? payload.sub : "";

  function handleLogout() {
    removeToken();
    router.replace("/login");
  }

  function toggleTheme() {
    setTheme(theme === "dark" ? "light" : "dark");
  }

  return (
    <header className="flex h-14 items-center gap-2 border-b border-border bg-background px-4">
      {/* Mobile menu button */}
      <Button
        variant="ghost"
        size="icon"
        className="md:hidden"
        onClick={onMobileMenuOpen}
        aria-label="打开菜单"
      >
        <Menu className="size-5" />
      </Button>

      {/* Desktop sidebar expand button (shown when collapsed) */}
      {sidebarCollapsed && (
        <Button
          variant="ghost"
          size="icon"
          className="hidden md:flex"
          onClick={onToggleSidebar}
          aria-label="展开侧边栏"
        >
          <PanelLeft className="size-5" />
        </Button>
      )}

      {/* Brand name */}
      <h1 className="text-base font-semibold tracking-tight text-foreground">
        赫尔墨斯 Hermes
      </h1>

      <div className="flex-1" />

      {/* Theme toggle */}
      {mounted && (
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleTheme}
          aria-label="切换主题"
        >
          {theme === "dark" ? (
            <Sun className="size-4" />
          ) : (
            <Moon className="size-4" />
          )}
        </Button>
      )}

      {/* User menu */}
      <DropdownMenu>
        <DropdownMenuTrigger
          className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent transition-colors outline-none"
        >
          <User className="size-4" />
          <span className="hidden sm:inline">{username || "用户"}</span>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" sideOffset={8}>
          <DropdownMenuGroup>
            <DropdownMenuLabel>{username || "用户"}</DropdownMenuLabel>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={handleLogout}>
            <LogOut className="size-4" />
            退出登录
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}
