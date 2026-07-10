"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { LogIn, UserPlus, AlertCircle } from "lucide-react";
import { post, setToken } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@/components/ui/card";

interface AuthResponse {
  access_token: string;
  token_type: string;
}

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleAuth(mode: "login" | "register") {
    setError("");
    if (!username.trim() || !password.trim()) {
      setError("请输入用户名和密码");
      return;
    }
    if (password.length < 6) {
      setError("密码至少需要6位");
      return;
    }

    setIsSubmitting(true);
    try {
      const endpoint =
        mode === "login" ? "/auth/login" : "/auth/register";
      const data = await post<AuthResponse>(endpoint, {
        username: username.trim(),
        password,
      });
      setToken(data.access_token);
      router.push("/");
    } catch (err: unknown) {
      if (
        err &&
        typeof err === "object" &&
        "response" in err &&
        err.response &&
        typeof err.response === "object" &&
        "data" in err.response
      ) {
        const resp = err.response as { data: { detail?: string } };
        setError(resp.data.detail || "操作失败，请重试");
      } else {
        setError("网络错误，请检查连接后重试");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-background to-muted px-4">
      <motion.div
        initial={{ opacity: 0, y: 32 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ type: "spring", stiffness: 260, damping: 24 }}
        className="w-full max-w-md"
      >
        <Card>
          <CardHeader className="text-center">
            <CardTitle className="text-2xl font-bold tracking-tight">
              赫尔墨斯 Hermes：你的智库文档 Agent
            </CardTitle>
            <CardDescription className="text-base">
              把文档变成答案，把知识变成行动
            </CardDescription>
          </CardHeader>

          <CardContent className="flex flex-col gap-4">
            {error && (
              <motion.div
                initial={{ opacity: 0, y: -8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ type: "spring", stiffness: 300, damping: 20 }}
                className="flex items-center gap-2 rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                <AlertCircle className="size-4 shrink-0" />
                <span>{error}</span>
              </motion.div>
            )}

            <div className="flex flex-col gap-2">
              <Label htmlFor="username">用户名</Label>
              <Input
                id="username"
                type="text"
                placeholder="请输入用户名"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleAuth("login");
                }}
                disabled={isSubmitting}
                autoComplete="username"
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="password">密码</Label>
              <Input
                id="password"
                type="password"
                placeholder="请输入密码（至少6位）"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleAuth("login");
                }}
                disabled={isSubmitting}
                autoComplete="current-password"
              />
            </div>
          </CardContent>

          <CardFooter className="flex flex-col gap-2 sm:flex-row">
            <Button
              className="w-full"
              size="lg"
              disabled={isSubmitting}
              onClick={() => handleAuth("login")}
            >
              <LogIn className="size-4" data-icon="inline-start" />
              登录
            </Button>
            <Button
              className="w-full"
              size="lg"
              variant="outline"
              disabled={isSubmitting}
              onClick={() => handleAuth("register")}
            >
              <UserPlus className="size-4" data-icon="inline-start" />
              注册
            </Button>
          </CardFooter>
        </Card>
      </motion.div>
    </div>
  );
}
