"use client";

import { useCallback, useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Users,
  MessageSquare,
  FileText,
  Trash2,
  Plus,
  Pencil,
  ThumbsUp,
  ThumbsDown,
  X,
  AlertCircle,
  CheckCircle2,
  Sparkles,
  Cloud,
  Bot,
  Hash,
  ScanText,
  Play,
  Star,
  Power,
  RefreshCw,
  Search,
  FlaskConical,
  Save,
  RotateCcw,
  Info,
} from "lucide-react";
import { get, post, put, del } from "@/lib/api";
import { useChatContext } from "@/lib/chat-context";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogClose,
} from "@/components/ui/dialog";
import { Slider } from "@/components/ui/slider";
import { Separator } from "@/components/ui/separator";
import Thinking from "@/components/thinking";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface AdminStats {
  user_count: number;
  conversation_count: number;
  document_count: number;
}

interface AdminUser {
  id: number;
  username: string;
  role: string;
  created_at: string;
}

interface AdminFeedback {
  id: number;
  message_id: number;
  user_id: number;
  rating: string;
  comment: string | null;
  created_at: string;
}

interface SkillFull {
  id: number;
  name: string;
  description: string;
  system_prompt: string;
  rules: string | null;
  icon: string | null;
  auto_detect_patterns: string[];
  created_at: string;
  updated_at: string;
}

interface SkillFormData {
  name: string;
  description: string;
  system_prompt: string;
  rules: string;
  icon: string;
  auto_detect_patterns: string;
}

const EMPTY_SKILL_FORM: SkillFormData = {
  name: "",
  description: "",
  system_prompt: "",
  rules: "",
  icon: "",
  auto_detect_patterns: "",
};

type ModelType = "llm" | "embedding" | "ocr";

interface ProviderFull {
  id: number;
  name: string;
  model_type: ModelType;
  provider_type: string;
  base_url: string;
  api_key_hint: string;
  model_name: string;
  max_tokens: number;
  timeout_seconds: number;
  is_default: boolean;
  is_active: boolean;
  created_at: string;
}

interface ProviderFormData {
  name: string;
  model_type: ModelType;
  provider_type: string;
  base_url: string;
  api_key: string;
  model_name: string;
  max_tokens: string;
  timeout_seconds: string;
  is_default: boolean;
}

/* ---- Retrieval types ---- */

type FusionMode = "weighted" | "rrf" | "semantic" | "bm25";

interface RetrievalSettings {
  mode: FusionMode;
  alpha: number;
  rrf_k: number;
  bm25_top_k: number;
  vector_top_k: number;
  final_top_k: number;
  semantic_threshold: number;
  enable_bm25: boolean;
  rerank_enabled: boolean;
  rerank_top_n: number;
  rerank_provider_id: number | null;
  contextual_chunking_enabled: boolean;
  contextual_chunking_provider_id: number | null;
  query_rewrite_simple_enabled: boolean;
  query_rewrite_hyde_enabled: boolean;
  query_rewrite_provider_id: number | null;
  // 分块
  chunk_size: number;
  chunk_overlap: number;
  splitter_strategy: "recursive" | "markdown" | "character" | "token";
  chunk_separators: string[] | null;
  // 生成
  gen_temperature: number;
  gen_top_p: number | null;
  gen_max_tokens: number | null;
  gen_presence_penalty: number;
  gen_frequency_penalty: number;
  gen_stop: string[] | null;
  max_context_length: number;
  max_history_messages: number;
  // Prompt 与拒答
  system_prompt_rag: string | null;
  system_prompt_direct: string | null;
  no_answer_text: string | null;
  allow_fallback_to_direct: boolean;
  updated_at: string;
}

interface PreviewItem {
  id: string;
  source: string;
  content: string;
  bm25_rank: number | null;
  bm25_norm: number | null;
  sem_rank: number | null;
  sem_norm: number | null;
  fused_score: number;
  original_rank: number | null;
  rerank_score: number | null;
}

interface PreviewResponse {
  mode: string;
  used_params: RetrievalSettings;
  items: PreviewItem[];
}

const EMPTY_PROVIDER_FORM: ProviderFormData = {
  name: "",
  model_type: "llm",
  provider_type: "remote",
  base_url: "https://api.deepseek.com/v1",
  api_key: "",
  model_name: "deepseek-chat",
  max_tokens: "4096",
  timeout_seconds: "120",
  is_default: false,
};

/** 不同模型类型的元信息（用于分组标题、图标、表单占位符）。 */
const MODEL_TYPE_META: Record<
  ModelType,
  {
    label: string;
    icon: React.ComponentType<{ className?: string }>;
    color: string;
    placeholder: { name: string; base_url: string; model_name: string };
  }
> = {
  llm: {
    label: "对话模型 (LLM)",
    icon: Bot,
    color: "text-emerald-600",
    placeholder: {
      name: "如：DeepSeek Chat",
      base_url: "https://api.deepseek.com/v1",
      model_name: "deepseek-chat",
    },
  },
  embedding: {
    label: "嵌入模型 (Embedding)",
    icon: Hash,
    color: "text-violet-600",
    placeholder: {
      name: "如：OpenAI Embedding",
      base_url: "https://api.openai.com/v1",
      model_name: "text-embedding-3-small",
    },
  },
  ocr: {
    label: "OCR 模型 (Vision)",
    icon: ScanText,
    color: "text-orange-600",
    placeholder: {
      name: "如：智谱 GLM-4V",
      base_url: "https://open.bigmodel.cn/api/paas/v4",
      model_name: "glm-4v-flash",
    },
  },
};

/* ------------------------------------------------------------------ */
/*  Stat card                                                          */
/* ------------------------------------------------------------------ */

function StatCard({
  title,
  value,
  icon: Icon,
}: {
  title: string;
  value: number;
  icon: React.ComponentType<{ className?: string }>;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: "spring", stiffness: 300, damping: 25 }}
    >
      <Card>
        <CardContent className="flex items-center gap-4 py-5">
          <div className="rounded-lg bg-primary/10 p-3">
            <Icon className="size-6 text-primary" />
          </div>
          <div>
            <p className="text-sm text-muted-foreground">{title}</p>
            <p className="text-2xl font-bold">{value}</p>
          </div>
        </CardContent>
      </Card>
    </motion.div>
  );
}

/* ------------------------------------------------------------------ */
/*  Module-level init fetcher (avoids React Compiler dep injection)    */
/* ------------------------------------------------------------------ */

async function initLoadAdmin(
  setStats: React.Dispatch<React.SetStateAction<AdminStats | null>>,
  setUsers: React.Dispatch<React.SetStateAction<AdminUser[]>>,
  setFeedbacks: React.Dispatch<React.SetStateAction<AdminFeedback[]>>,
  setSkills: React.Dispatch<React.SetStateAction<SkillFull[]>>,
  setProviders: React.Dispatch<React.SetStateAction<ProviderFull[]>>,
  setLoading: React.Dispatch<React.SetStateAction<boolean>>,
  setAccessDenied: React.Dispatch<React.SetStateAction<boolean>>,
) {
  try {
    const [statsRes, usersRes, feedbackRes, skillsRes, providersRes] = await Promise.all([
      get<AdminStats>("/api/admin/stats"),
      get<{ users: AdminUser[] }>("/api/admin/users"),
      get<{ feedbacks: AdminFeedback[] }>("/api/admin/feedback"),
      get<{ skills: SkillFull[] }>("/api/skills"),
      get<{ providers: ProviderFull[] }>("/api/admin/providers"),
    ]);
    setStats(statsRes);
    setUsers(usersRes.users ?? []);
    setFeedbacks(feedbackRes.feedbacks ?? []);
    setSkills(skillsRes.skills ?? []);
    setProviders(providersRes.providers ?? []);
  } catch (err: unknown) {
    const status = (err as { response?: { status?: number } })?.response?.status;
    if (status === 403) {
      setAccessDenied(true);
    }
  } finally {
    setLoading(false);
  }
}

/* ------------------------------------------------------------------ */
/*  Page                                                               */
/* ------------------------------------------------------------------ */

export default function AdminPage() {
  const {
    refreshProviders,
    refreshEmbeddingProviders,
    refreshOcrProviders,
  } = useChatContext();

  const [stats, setStats] = useState<AdminStats | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [feedbacks, setFeedbacks] = useState<AdminFeedback[]>([]);
  const [skills, setSkills] = useState<SkillFull[]>([]);
  const [loading, setLoading] = useState(true);
  const [accessDenied, setAccessDenied] = useState(false);

  const [providers, setProviders] = useState<ProviderFull[]>([]);

  const [skillDialogOpen, setSkillDialogOpen] = useState(false);
  const [editingSkill, setEditingSkill] = useState<SkillFull | null>(null);
  const [skillForm, setSkillForm] = useState<SkillFormData>(EMPTY_SKILL_FORM);
  const [skillSaving, setSkillSaving] = useState(false);

  const [providerDialogOpen, setProviderDialogOpen] = useState(false);
  const [editingProvider, setEditingProvider] = useState<ProviderFull | null>(null);
  const [providerForm, setProviderForm] = useState<ProviderFormData>(EMPTY_PROVIDER_FORM);
  const [providerSaving, setProviderSaving] = useState(false);
  const [testingProvider, setTestingProvider] = useState<number | null>(null);
  const [rebuildingVS, setRebuildingVS] = useState(false);

  /* ---- Retrieval tuning state ---- */
  const [retSettings, setRetSettings] = useState<RetrievalSettings | null>(null);
  const [retDraft, setRetDraft] = useState<Partial<RetrievalSettings>>({});
  const [retSaving, setRetSaving] = useState(false);
  const [previewQuery, setPreviewQuery] = useState("");
  const [previewItems, setPreviewItems] = useState<PreviewItem[]>([]);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewUsedMode, setPreviewUsedMode] = useState<string>("");

  const [toast, setToast] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const showToast = useCallback((type: "success" | "error", text: string) => {
    setToast({ type, text });
    setTimeout(() => setToast(null), 3000);
  }, []);

  useEffect(() => {
    initLoadAdmin(setStats, setUsers, setFeedbacks, setSkills, setProviders, setLoading, setAccessDenied);
    // Load retrieval settings
    (async () => {
      try {
        const s = await get<RetrievalSettings>("/api/admin/retrieval");
        setRetSettings(s);
        setRetDraft(s);
      } catch {
        /* admin might not have access yet */
      }
    })();
  }, []);

  /* ---- delete user ---- */
  async function handleDeleteUser(userId: number) {
    try {
      await del(`/api/admin/users/${userId}`);
      showToast("success", "用户已删除");
      setUsers((prev) => prev.filter((u) => u.id !== userId));
    } catch {
      showToast("error", "删除用户失败");
    }
  }

  /* ---- skill CRUD ---- */
  function openCreateSkill() {
    setEditingSkill(null);
    setSkillForm(EMPTY_SKILL_FORM);
    setSkillDialogOpen(true);
  }

  function openEditSkill(skill: SkillFull) {
    setEditingSkill(skill);
    setSkillForm({
      name: skill.name,
      description: skill.description ?? "",
      system_prompt: skill.system_prompt,
      rules: skill.rules ?? "",
      icon: skill.icon ?? "",
      auto_detect_patterns: (skill.auto_detect_patterns ?? []).join(", "),
    });
    setSkillDialogOpen(true);
  }

  async function handleSaveSkill() {
    if (!skillForm.name.trim() || !skillForm.system_prompt.trim()) {
      showToast("error", "名称和系统提示词不能为空");
      return;
    }
    const patterns = skillForm.auto_detect_patterns
      .split(/[,，\n]/)
      .map((s) => s.trim())
      .filter(Boolean);
    setSkillSaving(true);
    try {
      if (editingSkill) {
        await put(`/api/admin/skills/${editingSkill.id}`, {
          name: skillForm.name,
          description: skillForm.description,
          system_prompt: skillForm.system_prompt,
          rules: skillForm.rules || null,
          icon: skillForm.icon || null,
          auto_detect_patterns: patterns,
        });
        showToast("success", "技能已更新");
      } else {
        await post("/api/admin/skills", {
          name: skillForm.name,
          description: skillForm.description,
          system_prompt: skillForm.system_prompt,
          rules: skillForm.rules || null,
          icon: skillForm.icon || null,
          auto_detect_patterns: patterns,
        });
        showToast("success", "技能已创建");
      }
      setSkillDialogOpen(false);
      const skillsRes = await get<{ skills: SkillFull[] }>("/api/skills");
      setSkills(skillsRes.skills ?? []);
    } catch {
      showToast("error", editingSkill ? "更新技能失败" : "创建技能失败");
    } finally {
      setSkillSaving(false);
    }
  }

  async function handleDeleteSkill(skillId: number) {
    try {
      await del(`/api/admin/skills/${skillId}`);
      showToast("success", "技能已删除");
      setSkills((prev) => prev.filter((s) => s.id !== skillId));
    } catch {
      showToast("error", "删除技能失败");
    }
  }

  /* ---- provider CRUD ---- */
  function openCreateProvider() {
    setEditingProvider(null);
    setProviderForm(EMPTY_PROVIDER_FORM);
    setProviderDialogOpen(true);
  }

  function openEditProvider(p: ProviderFull) {
    setEditingProvider(p);
    setProviderForm({
      name: p.name,
      model_type: p.model_type,
      provider_type: p.provider_type,
      base_url: p.base_url,
      api_key: "",
      model_name: p.model_name,
      max_tokens: String(p.max_tokens),
      timeout_seconds: String(p.timeout_seconds),
      is_default: p.is_default,
    });
    setProviderDialogOpen(true);
  }

  function applyTypeDefaults(t: ModelType) {
    const meta = MODEL_TYPE_META[t];
    setProviderForm((f) => ({
      ...f,
      model_type: t,
      base_url: meta.placeholder.base_url,
      model_name: meta.placeholder.model_name,
    }));
  }

  async function handleSaveProvider() {
    if (!providerForm.name.trim() || !providerForm.base_url.trim() || !providerForm.model_name.trim()) {
      showToast("error", "名称、API 地址和模型名不能为空");
      return;
    }
    if (!editingProvider && !providerForm.api_key.trim()) {
      showToast("error", "新建 Provider 必须填写 API Key");
      return;
    }
    setProviderSaving(true);
    try {
      const payload = {
        name: providerForm.name,
        model_type: providerForm.model_type,
        provider_type: providerForm.provider_type,
        base_url: providerForm.base_url,
        api_key: providerForm.api_key || "",
        model_name: providerForm.model_name,
        max_tokens: parseInt(providerForm.max_tokens) || 4096,
        timeout_seconds: parseInt(providerForm.timeout_seconds) || 120,
        is_default: providerForm.is_default,
      };
      if (editingProvider) {
        await put(`/api/admin/providers/${editingProvider.id}`, payload);
        showToast("success", "模型配置已更新");
      } else {
        await post("/api/admin/providers", payload);
        showToast("success", "模型配置已创建");
      }
      setProviderDialogOpen(false);
      const res = await get<{ providers: ProviderFull[] }>("/api/admin/providers");
      setProviders(res.providers ?? []);
      // 同步到对话页面的全局 provider 状态
      const type = providerForm.model_type;
      if (type === "llm") refreshProviders();
      else if (type === "embedding") refreshEmbeddingProviders();
      else refreshOcrProviders();
    } catch {
      showToast("error", editingProvider ? "更新失败" : "创建失败");
    } finally {
      setProviderSaving(false);
    }
  }

  async function handleDeleteProvider(id: number) {
    try {
      const target = providers.find((p) => p.id === id);
      await del(`/api/admin/providers/${id}`);
      showToast("success", "模型配置已删除");
      setProviders((prev) => prev.filter((p) => p.id !== id));
      // 同步到对话页面的全局 provider 状态
      if (target?.model_type === "llm") refreshProviders();
      else if (target?.model_type === "embedding") refreshEmbeddingProviders();
      else if (target?.model_type === "ocr") refreshOcrProviders();
    } catch {
      showToast("error", "删除失败（默认模型不可删除）");
    }
  }

  async function handleTestProvider(id: number) {
    setTestingProvider(id);
    try {
      const res = await post<{ success: boolean; message: string; latency_ms?: number }>(
        `/api/admin/providers/${id}/test`,
        {}
      );
      if (res.success) {
        showToast("success", `连通成功 (${res.latency_ms?.toFixed(0)}ms)`);
      } else {
        showToast("error", res.message);
      }
    } catch {
      showToast("error", "测试请求失败");
    } finally {
      setTestingProvider(null);
    }
  }

  async function handleToggleDefault(id: number) {
    try {
      await put(`/api/admin/providers/${id}`, { is_default: true });
      const res = await get<{ providers: ProviderFull[] }>("/api/admin/providers");
      setProviders(res.providers ?? []);
      showToast("success", "已设为默认模型");
      // 同步到对话页面的全局 provider 状态
      const target = res.providers.find((p) => p.id === id);
      if (target?.model_type === "llm") refreshProviders();
      else if (target?.model_type === "embedding") refreshEmbeddingProviders();
      else refreshOcrProviders();
    } catch {
      showToast("error", "设置默认失败");
    }
  }

  async function handleToggleActive(p: ProviderFull) {
    try {
      await put(`/api/admin/providers/${p.id}`, { is_active: !p.is_active });
      const res = await get<{ providers: ProviderFull[] }>("/api/admin/providers");
      setProviders(res.providers ?? []);
      showToast("success", p.is_active ? "已停用" : "已启用");
      // 同步到对话页面的全局 provider 状态
      if (p.model_type === "llm") refreshProviders();
      else if (p.model_type === "embedding") refreshEmbeddingProviders();
      else refreshOcrProviders();
    } catch {
      showToast("error", "操作失败");
    }
  }

  /* ---- retrieval tuning ---- */
  async function handleSaveRetrieval() {
    setRetSaving(true);
    try {
      const res = await put<RetrievalSettings>("/api/admin/retrieval", retDraft);
      setRetSettings(res);
      setRetDraft(res);
      showToast("success", "检索参数已保存");
    } catch {
      showToast("error", "保存检索参数失败");
    } finally {
      setRetSaving(false);
    }
  }

  function handleResetRetrieval() {
    if (retSettings) setRetDraft({ ...retSettings });
  }

  async function handlePreview() {
    if (!previewQuery.trim()) return;
    setPreviewLoading(true);
    setPreviewItems([]);
    try {
      const overrides: Record<string, unknown> = {};
      if (retDraft.mode) overrides.mode = retDraft.mode;
      if (retDraft.alpha != null) overrides.alpha = retDraft.alpha;
      if (retDraft.rrf_k != null) overrides.rrf_k = retDraft.rrf_k;
      if (retDraft.bm25_top_k != null) overrides.bm25_top_k = retDraft.bm25_top_k;
      if (retDraft.vector_top_k != null) overrides.vector_top_k = retDraft.vector_top_k;
      if (retDraft.final_top_k != null) overrides.final_top_k = retDraft.final_top_k;
      if (retDraft.semantic_threshold != null) overrides.semantic_threshold = retDraft.semantic_threshold;
      if (retDraft.rerank_enabled != null) overrides.rerank_enabled = retDraft.rerank_enabled;
      if (retDraft.rerank_top_n != null) overrides.rerank_top_n = retDraft.rerank_top_n;
      const res = await post<PreviewResponse>("/api/admin/retrieval/preview", {
        query: previewQuery.trim(),
        params: Object.keys(overrides).length > 0 ? overrides : undefined,
      });
      setPreviewItems(res.items);
      setPreviewUsedMode(res.mode);
    } catch {
      showToast("error", "检索预览失败，请确认知识库中已有文档");
    } finally {
      setPreviewLoading(false);
    }
  }

  async function handleRebuildVectorStore() {
    if (
      !confirm(
        "将清空整个向量库并按当前默认嵌入模型把 uploads/ 中的所有文件重新切片入库。\n" +
          "这通常用于切换嵌入模型后维度不匹配。\n\n确定继续？"
      )
    )
      return;
    setRebuildingVS(true);
    try {
      const res = await post<{
        success: boolean;
        message: string;
        documents: number;
        chunks: number;
      }>("/api/admin/vectorstore/rebuild", {});
      showToast(res.success ? "success" : "error", res.message);
    } catch {
      showToast("error", "重建请求失败");
    } finally {
      setRebuildingVS(false);
    }
  }

  /* ---- access denied ---- */
  if (accessDenied) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-center space-y-2">
          <AlertCircle className="size-12 mx-auto text-destructive opacity-60" />
          <p className="text-lg font-medium">权限不足</p>
          <p className="text-sm text-muted-foreground">仅管理员可访问此页面</p>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Thinking />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 space-y-6">
      <h1 className="text-2xl font-bold text-foreground">管理后台</h1>

      {/* Stats */}
      {stats && (
        <div className="grid gap-4 sm:grid-cols-3">
          <StatCard title="用户总数" value={stats.user_count} icon={Users} />
          <StatCard title="对话总数" value={stats.conversation_count} icon={MessageSquare} />
          <StatCard title="文档总数" value={stats.document_count} icon={FileText} />
        </div>
      )}

      {/* Tabs */}
      <Tabs defaultValue="users">
        <TabsList>
          <TabsTrigger value="users">用户管理</TabsTrigger>
          <TabsTrigger value="feedback">反馈列表</TabsTrigger>
          <TabsTrigger value="skills">技能管理</TabsTrigger>
          <TabsTrigger value="providers">模型管理</TabsTrigger>
          <TabsTrigger value="retrieval">检索调参</TabsTrigger>
        </TabsList>

        {/* Users tab */}
        <TabsContent value="users" className="mt-4">
          {users.length === 0 ? (
            <p className="text-center py-8 text-muted-foreground">暂无用户</p>
          ) : (
            <div className="rounded-lg border overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-muted/50">
                    <tr>
                      <th className="px-4 py-3 text-left font-medium">ID</th>
                      <th className="px-4 py-3 text-left font-medium">用户名</th>
                      <th className="px-4 py-3 text-left font-medium">角色</th>
                      <th className="px-4 py-3 text-left font-medium">注册时间</th>
                      <th className="px-4 py-3 text-right font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {users.map((user) => (
                      <motion.tr
                        key={user.id}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        transition={{ type: "spring", stiffness: 300, damping: 25 }}
                        className="hover:bg-muted/30 transition-colors"
                      >
                        <td className="px-4 py-3">{user.id}</td>
                        <td className="px-4 py-3 font-medium">{user.username}</td>
                        <td className="px-4 py-3">
                          <Badge variant={user.role === "admin" ? "default" : "secondary"}>
                            {user.role}
                          </Badge>
                        </td>
                        <td className="px-4 py-3 text-muted-foreground">
                          {new Date(user.created_at).toLocaleString("zh-CN")}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-destructive hover:text-destructive"
                            onClick={() => handleDeleteUser(user.id)}
                            disabled={user.role === "admin"}
                          >
                            <Trash2 className="size-3.5 mr-1" />删除
                          </Button>
                        </td>
                      </motion.tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </TabsContent>

        {/* Feedback tab */}
        <TabsContent value="feedback" className="mt-4">
          {feedbacks.length === 0 ? (
            <p className="text-center py-8 text-muted-foreground">暂无反馈</p>
          ) : (
            <div className="space-y-3">
              <AnimatePresence mode="popLayout">
                {feedbacks.map((fb) => (
                  <motion.div
                    key={fb.id}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ type: "spring", stiffness: 300, damping: 25 }}
                  >
                    <Card>
                      <CardContent className="py-3 flex items-start gap-4">
                        <div className="shrink-0 pt-0.5">
                          {fb.rating === "up" ? (
                            <ThumbsUp className="size-5 text-green-600" />
                          ) : (
                            <ThumbsDown className="size-5 text-red-500" />
                          )}
                        </div>
                        <div className="flex-1 min-w-0 space-y-1">
                          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                            <span>消息 #{fb.message_id}</span>
                            <span>用户 #{fb.user_id}</span>
                            <span>{new Date(fb.created_at).toLocaleString("zh-CN")}</span>
                          </div>
                          {fb.comment && (
                            <p className="text-sm">{fb.comment}</p>
                          )}
                        </div>
                      </CardContent>
                    </Card>
                  </motion.div>
                ))}
              </AnimatePresence>
            </div>
          )}
        </TabsContent>

        {/* Skills tab */}
        <TabsContent value="skills" className="mt-4 space-y-4">
          <div className="flex justify-end">
            <Button onClick={openCreateSkill}>
              <Plus className="size-4 mr-2" />新建技能
            </Button>
          </div>
          {skills.length === 0 ? (
            <p className="text-center py-8 text-muted-foreground">暂无技能</p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              <AnimatePresence mode="popLayout">
                {skills.map((skill) => (
                  <motion.div
                    key={skill.id}
                    layout
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    transition={{ type: "spring", stiffness: 300, damping: 25 }}
                  >
                    <Card>
                      <CardHeader className="pb-2">
                        <CardTitle className="flex items-center gap-2 text-sm">
                          <span className="text-lg">{skill.icon || "🔧"}</span>
                          <span className="flex-1">{skill.name}</span>
                          <Sparkles className="size-3.5 text-primary" />
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-2">
                        {skill.description && (
                          <p className="text-xs text-muted-foreground line-clamp-2">
                            {skill.description}
                          </p>
                        )}
                        <p className="text-xs text-muted-foreground line-clamp-2 font-mono">
                          提示词: {skill.system_prompt.slice(0, 80)}...
                        </p>
                        <div className="flex gap-1.5 pt-1">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => openEditSkill(skill)}
                          >
                            <Pencil className="size-3.5 mr-1" />编辑
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-destructive hover:text-destructive"
                            onClick={() => handleDeleteSkill(skill.id)}
                          >
                            <Trash2 className="size-3.5 mr-1" />删除
                          </Button>
                        </div>
                      </CardContent>
                    </Card>
                  </motion.div>
                ))}
              </AnimatePresence>
            </div>
          )}
        </TabsContent>

        {/* Providers tab — 按 model_type 分组展示 */}
        <TabsContent value="providers" className="mt-4 space-y-6">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <p className="text-sm text-muted-foreground flex-1 min-w-[260px]">
              所有模型均通过远程 OpenAI 兼容 API 调用。请分别配置 LLM、嵌入和 OCR 模型。
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                onClick={handleRebuildVectorStore}
                disabled={rebuildingVS}
                title="切换嵌入模型后若维度不匹配，点此清空并按当前嵌入模型重建"
              >
                <RefreshCw className={`size-4 mr-2 ${rebuildingVS ? "animate-spin" : ""}`} />
                {rebuildingVS ? "重建中..." : "重建向量库"}
              </Button>
              <Button onClick={openCreateProvider}>
                <Plus className="size-4 mr-2" />新增模型
              </Button>
            </div>
          </div>

          {(["llm", "embedding", "ocr"] as ModelType[]).map((type) => {
            const meta = MODEL_TYPE_META[type];
            const TypeIcon = meta.icon;
            const items = providers.filter((p) => p.model_type === type);
            return (
              <div key={type} className="space-y-2">
                <div className="flex items-center gap-2">
                  <TypeIcon className={`size-4 ${meta.color}`} />
                  <h3 className="text-sm font-semibold">{meta.label}</h3>
                  <Badge variant="outline" className="text-[10px]">{items.length}</Badge>
                </div>
                {items.length === 0 ? (
                  <p className="text-xs text-muted-foreground py-3">
                    暂无 {meta.label}，点击右上角「新增模型」并选择「{meta.label}」即可创建。
                  </p>
                ) : (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <AnimatePresence mode="popLayout">
                      {items.map((p) => (
                        <motion.div
                          key={p.id}
                          layout
                          initial={{ opacity: 0, scale: 0.95 }}
                          animate={{ opacity: 1, scale: 1 }}
                          exit={{ opacity: 0, scale: 0.95 }}
                          transition={{ type: "spring", stiffness: 300, damping: 25 }}
                        >
                          <Card className={!p.is_active ? "opacity-50" : ""}>
                            <CardHeader className="pb-2">
                              <CardTitle className="flex items-center gap-2 text-sm">
                                <Cloud className="size-4 text-emerald-600" />
                                <span className="flex-1 truncate">{p.name}</span>
                                {p.is_default && (
                                  <Badge variant="default" className="text-[10px]">默认</Badge>
                                )}
                                <Badge variant="outline" className={`text-[10px] ${meta.color}`}>
                                  {meta.label.split(" ")[0]}
                                </Badge>
                              </CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-2">
                              <div className="text-xs space-y-1 text-muted-foreground">
                                <p>模型: <span className="font-mono text-foreground">{p.model_name}</span></p>
                                <p>地址: <span className="font-mono text-foreground break-all">{p.base_url}</span></p>
                                <p>Key: <span className="font-mono">{p.api_key_hint}</span></p>
                                <p>max_tokens: {p.max_tokens} | 超时: {p.timeout_seconds}s</p>
                              </div>
                              <div className="flex flex-wrap gap-1.5 pt-1">
                                <Button variant="ghost" size="sm" onClick={() => openEditProvider(p)}>
                                  <Pencil className="size-3.5 mr-1" />编辑
                                </Button>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => handleTestProvider(p.id)}
                                  disabled={testingProvider === p.id}
                                >
                                  <Play className="size-3.5 mr-1" />
                                  {testingProvider === p.id ? "测试中..." : "测试"}
                                </Button>
                                {!p.is_default && (
                                  <Button variant="ghost" size="sm" onClick={() => handleToggleDefault(p.id)}>
                                    <Star className="size-3.5 mr-1" />设为默认
                                  </Button>
                                )}
                                <Button variant="ghost" size="sm" onClick={() => handleToggleActive(p)}>
                                  <Power className="size-3.5 mr-1" />{p.is_active ? "停用" : "启用"}
                                </Button>
                                {!p.is_default && (
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    className="text-destructive hover:text-destructive"
                                    onClick={() => handleDeleteProvider(p.id)}
                                  >
                                    <Trash2 className="size-3.5 mr-1" />删除
                                  </Button>
                                )}
                              </div>
                            </CardContent>
                          </Card>
                        </motion.div>
                      ))}
                    </AnimatePresence>
                  </div>
                )}
              </div>
            );
          })}
        </TabsContent>

        {/* Retrieval tuning tab */}
        <TabsContent value="retrieval" className="mt-4">
          {!retSettings ? (
            <p className="text-center py-8 text-muted-foreground">加载中...</p>
          ) : (
            <div className="grid gap-6 lg:grid-cols-2">
              {/* Left: parameters panel */}
              <Card>
                <CardHeader className="pb-4">
                  <CardTitle className="flex items-center gap-2 text-lg">
                    <FlaskConical className="size-5 text-violet-600" />
                    检索参数配置
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-5">
                  {/* Mode selector */}
                  <div className="space-y-2">
                    <Label className="font-medium">融合策略</Label>
                    <div className="grid grid-cols-4 gap-2">
                      {(["weighted", "rrf", "semantic", "bm25"] as FusionMode[]).map((m) => {
                        const active = (retDraft.mode ?? retSettings.mode) === m;
                        const labels: Record<FusionMode, string> = {
                          weighted: "加权融合",
                          rrf: "RRF 排名",
                          semantic: "纯语义",
                          bm25: "纯关键词",
                        };
                        return (
                          <button
                            key={m}
                            onClick={() => setRetDraft((d) => ({ ...d, mode: m }))}
                            className={`rounded-md border px-2 py-2 text-xs transition-colors ${
                              active
                                ? "border-primary bg-primary/5 text-foreground font-medium"
                                : "border-input text-muted-foreground hover:border-primary/40"
                            }`}
                          >
                            {labels[m]}
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  {/* Alpha slider (only for weighted) */}
                  {(retDraft.mode ?? retSettings.mode) === "weighted" && (
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <Label className="font-medium">Alpha 权重</Label>
                        <span className="text-xs tabular-nums text-muted-foreground">
                          语义 {((retDraft.alpha ?? retSettings.alpha) * 100).toFixed(0)}% / 关键词 {((1 - (retDraft.alpha ?? retSettings.alpha)) * 100).toFixed(0)}%
                        </span>
                      </div>
                      <Slider
                        value={[(retDraft.alpha ?? retSettings.alpha) * 100]}
                        min={0}
                        max={100}
                        step={5}
                        onValueChange={(v: unknown) => {
                          const num = Array.isArray(v) ? (v[0] as number) : (v as number);
                          setRetDraft((d) => ({ ...d, alpha: num / 100 }));
                        }}
                      />
                      <div className="flex justify-between text-[10px] text-muted-foreground">
                        <span>BM25 优先</span>
                        <span>语义优先</span>
                      </div>
                    </div>
                  )}

                  {/* RRF K (only for rrf) */}
                  {(retDraft.mode ?? retSettings.mode) === "rrf" && (
                    <div className="space-y-2">
                      <Label className="font-medium">RRF 常数 k</Label>
                      <Input
                        type="number"
                        value={retDraft.rrf_k ?? retSettings.rrf_k}
                        onChange={(e) => setRetDraft((d) => ({ ...d, rrf_k: parseInt(e.target.value) || 60 }))}
                        min={1}
                        max={200}
                      />
                      <p className="text-[11px] text-muted-foreground">公式 1/(k+rank)，k 越大差距越小。常用 60。</p>
                    </div>
                  )}

                  <Separator />

                  {/* Recall counts */}
                  <div className="grid grid-cols-3 gap-3">
                    <div className="space-y-1.5">
                      <Label className="text-xs">BM25 召回数</Label>
                      <Input
                        type="number"
                        value={retDraft.bm25_top_k ?? retSettings.bm25_top_k}
                        onChange={(e) => setRetDraft((d) => ({ ...d, bm25_top_k: parseInt(e.target.value) || 20 }))}
                        min={1}
                        max={100}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-xs">向量召回数</Label>
                      <Input
                        type="number"
                        value={retDraft.vector_top_k ?? retSettings.vector_top_k}
                        onChange={(e) => setRetDraft((d) => ({ ...d, vector_top_k: parseInt(e.target.value) || 20 }))}
                        min={1}
                        max={100}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-xs">最终保留数</Label>
                      <Input
                        type="number"
                        value={retDraft.final_top_k ?? retSettings.final_top_k}
                        onChange={(e) => setRetDraft((d) => ({ ...d, final_top_k: parseInt(e.target.value) || 5 }))}
                        min={1}
                        max={20}
                      />
                    </div>
                  </div>

                  {/* Semantic threshold */}
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <Label className="font-medium">语义相关度阈值</Label>
                      <span className="text-xs tabular-nums text-muted-foreground">
                        {(retDraft.semantic_threshold ?? retSettings.semantic_threshold).toFixed(2)}
                      </span>
                    </div>
                    <Slider
                      value={[(retDraft.semantic_threshold ?? retSettings.semantic_threshold) * 100]}
                      min={0}
                      max={100}
                      step={5}
                      onValueChange={(v: unknown) => {
                        const num = Array.isArray(v) ? (v[0] as number) : (v as number);
                        setRetDraft((d) => ({ ...d, semantic_threshold: num / 100 }));
                      }}
                    />
                    <p className="text-[11px] text-muted-foreground">低于此分的片段不进入上下文。0 = 不过滤。</p>
                  </div>

                  <Separator />

                  {/* BM25 toggle */}
                  <div className="flex items-center justify-between">
                    <div>
                      <Label className="font-medium">启用 BM25 关键词检索</Label>
                      <p className="text-[11px] text-muted-foreground mt-0.5">关闭后仅使用语义向量检索</p>
                    </div>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={retDraft.enable_bm25 ?? retSettings.enable_bm25}
                      onClick={() => setRetDraft((d) => ({ ...d, enable_bm25: !(d.enable_bm25 ?? retSettings!.enable_bm25) }))}
                      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border transition-colors ${
                        (retDraft.enable_bm25 ?? retSettings.enable_bm25)
                          ? "bg-primary border-primary"
                          : "bg-muted border-input"
                      }`}
                    >
                      <span className={`inline-block size-3.5 transform rounded-full bg-white shadow-sm transition-transform ${
                        (retDraft.enable_bm25 ?? retSettings.enable_bm25) ? "translate-x-4" : "translate-x-0.5"
                      }`} />
                    </button>
                  </div>

                  {/* Rerank toggle */}
                  <div className="flex items-center justify-between">
                    <div>
                      <Label className="font-medium">启用 LLM 精排 (Rerank)</Label>
                      <p className="text-[11px] text-muted-foreground mt-0.5">召回后用 LLM 对片段重新打分排序</p>
                    </div>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={retDraft.rerank_enabled ?? retSettings.rerank_enabled}
                      onClick={() => setRetDraft((d) => ({ ...d, rerank_enabled: !(d.rerank_enabled ?? retSettings!.rerank_enabled) }))}
                      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border transition-colors ${
                        (retDraft.rerank_enabled ?? retSettings.rerank_enabled)
                          ? "bg-primary border-primary"
                          : "bg-muted border-input"
                      }`}
                    >
                      <span className={`inline-block size-3.5 transform rounded-full bg-white shadow-sm transition-transform ${
                        (retDraft.rerank_enabled ?? retSettings.rerank_enabled) ? "translate-x-4" : "translate-x-0.5"
                      }`} />
                    </button>
                  </div>

                  {(retDraft.rerank_enabled ?? retSettings.rerank_enabled) && (
                    <div className="space-y-1.5 pl-4 border-l-2 border-primary/20">
                      <Label className="text-xs">精排保留数</Label>
                      <Input
                        type="number"
                        value={retDraft.rerank_top_n ?? retSettings.rerank_top_n}
                        onChange={(e) => setRetDraft((d) => ({ ...d, rerank_top_n: parseInt(e.target.value) || 5 }))}
                        min={1}
                        max={20}
                        className="w-24"
                      />
                    </div>
                  )}

                  <Separator />

                  {/* Contextual chunking toggle */}
                  <div className="flex items-center justify-between">
                    <div>
                      <Label className="font-medium">启用上下文感知分块</Label>
                      <p className="text-[11px] text-muted-foreground mt-0.5">
                        入库时 LLM 为每个切片生成 50-100 字上下文摘要并前置到嵌入文本，提升小片段在长文档中的检索精度
                      </p>
                    </div>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={retDraft.contextual_chunking_enabled ?? retSettings.contextual_chunking_enabled}
                      onClick={() => setRetDraft((d) => ({ ...d, contextual_chunking_enabled: !(d.contextual_chunking_enabled ?? retSettings!.contextual_chunking_enabled) }))}
                      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border transition-colors ${
                        (retDraft.contextual_chunking_enabled ?? retSettings.contextual_chunking_enabled)
                          ? "bg-primary border-primary"
                          : "bg-muted border-input"
                      }`}
                    >
                      <span className={`inline-block size-3.5 transform rounded-full bg-white shadow-sm transition-transform ${
                        (retDraft.contextual_chunking_enabled ?? retSettings.contextual_chunking_enabled) ? "translate-x-4" : "translate-x-0.5"
                      }`} />
                    </button>
                  </div>

                  {(retDraft.contextual_chunking_enabled ?? retSettings.contextual_chunking_enabled) && (
                    <div className="space-y-2 pl-4 border-l-2 border-primary/20">
                      <Label className="text-xs">上下文生成 Provider</Label>
                      <select
                        value={retDraft.contextual_chunking_provider_id ?? retSettings.contextual_chunking_provider_id ?? 0}
                        onChange={(e) => {
                          const v = parseInt(e.target.value);
                          setRetDraft((d) => ({
                            ...d,
                            contextual_chunking_provider_id: v > 0 ? v : null,
                          }));
                        }}
                        className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-primary"
                      >
                        <option value={0}>与主 LLM 同 Provider</option>
                        {providers
                          .filter((p) => p.model_type === "llm" && p.is_active)
                          .map((p) => (
                            <option key={p.id} value={p.id}>
                              {p.name} ({p.model_name})
                            </option>
                          ))}
                      </select>
                      <div className="flex items-start gap-2 rounded-md border bg-amber-500/10 px-2 py-1.5">
                        <AlertCircle className="size-3.5 text-amber-600 shrink-0 mt-0.5" />
                        <p className="text-[11px] text-amber-700 dark:text-amber-400 leading-relaxed">
                          仅对新入库 / 重新入库的文档生效。每个切片增加 1 次 LLM 调用，入库耗时增加 5-15 倍。切换设置后需点上方「重建向量库」才能让旧文档全部生效。
                        </p>
                      </div>
                    </div>
                  )}

                  <Separator />

                  {/* Query rewrite block: 2 independent toggles + shared provider */}
                  <div className="space-y-3">
                    <div className="flex items-center gap-2">
                      <Label className="font-medium">查询重写</Label>
                      <Badge variant="outline" className="text-[10px]">问答时增强</Badge>
                    </div>

                    {/* Simple rewrite toggle */}
                    <div className="flex items-center justify-between">
                      <div>
                        <Label className="text-sm">简单重写</Label>
                        <p className="text-[11px] text-muted-foreground mt-0.5">
                          口语化 / 模糊查询 → LLM 改写为含关键术语的检索查询；BM25 与向量两路都使用重写后的查询
                        </p>
                      </div>
                      <button
                        type="button"
                        role="switch"
                        aria-checked={retDraft.query_rewrite_simple_enabled ?? retSettings.query_rewrite_simple_enabled}
                        onClick={() => setRetDraft((d) => ({ ...d, query_rewrite_simple_enabled: !(d.query_rewrite_simple_enabled ?? retSettings!.query_rewrite_simple_enabled) }))}
                        className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border transition-colors ${
                          (retDraft.query_rewrite_simple_enabled ?? retSettings.query_rewrite_simple_enabled)
                            ? "bg-primary border-primary"
                            : "bg-muted border-input"
                        }`}
                      >
                        <span className={`inline-block size-3.5 transform rounded-full bg-white shadow-sm transition-transform ${
                          (retDraft.query_rewrite_simple_enabled ?? retSettings.query_rewrite_simple_enabled) ? "translate-x-4" : "translate-x-0.5"
                        }`} />
                      </button>
                    </div>

                    {/* HyDE toggle */}
                    <div className="flex items-center justify-between">
                      <div>
                        <Label className="text-sm">HyDE 假设性答案</Label>
                        <p className="text-[11px] text-muted-foreground mt-0.5">
                          LLM 先写一段假设性答案 → 用其语义向量做检索；向量通道改用 HyDE 文本，BM25 通道仍用原查询（或简单重写结果）
                        </p>
                      </div>
                      <button
                        type="button"
                        role="switch"
                        aria-checked={retDraft.query_rewrite_hyde_enabled ?? retSettings.query_rewrite_hyde_enabled}
                        onClick={() => setRetDraft((d) => ({ ...d, query_rewrite_hyde_enabled: !(d.query_rewrite_hyde_enabled ?? retSettings!.query_rewrite_hyde_enabled) }))}
                        className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border transition-colors ${
                          (retDraft.query_rewrite_hyde_enabled ?? retSettings.query_rewrite_hyde_enabled)
                            ? "bg-primary border-primary"
                            : "bg-muted border-input"
                        }`}
                      >
                        <span className={`inline-block size-3.5 transform rounded-full bg-white shadow-sm transition-transform ${
                          (retDraft.query_rewrite_hyde_enabled ?? retSettings.query_rewrite_hyde_enabled) ? "translate-x-4" : "translate-x-0.5"
                        }`} />
                      </button>
                    </div>

                    {/* Shared provider selector + 提示 */}
                    {((retDraft.query_rewrite_simple_enabled ?? retSettings.query_rewrite_simple_enabled) ||
                      (retDraft.query_rewrite_hyde_enabled ?? retSettings.query_rewrite_hyde_enabled)) && (
                      <div className="space-y-2 pl-4 border-l-2 border-primary/20">
                        <Label className="text-xs">查询重写 Provider（两功能共用）</Label>
                        <select
                          value={retDraft.query_rewrite_provider_id ?? retSettings.query_rewrite_provider_id ?? 0}
                          onChange={(e) => {
                            const v = parseInt(e.target.value);
                            setRetDraft((d) => ({
                              ...d,
                              query_rewrite_provider_id: v > 0 ? v : null,
                            }));
                          }}
                          className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-primary"
                        >
                          <option value={0}>与主 LLM 同 Provider</option>
                          {providers
                            .filter((p) => p.model_type === "llm" && p.is_active)
                            .map((p) => (
                              <option key={p.id} value={p.id}>
                                {p.name} ({p.model_name})
                              </option>
                            ))}
                        </select>
                        <div className="flex items-start gap-2 rounded-md border bg-blue-500/10 px-2 py-1.5">
                          <Info className="size-3.5 text-blue-600 shrink-0 mt-0.5" />
                          <p className="text-[11px] text-blue-700 dark:text-blue-400 leading-relaxed">
                            每次问答额外消耗 1 次 LLM 调用。重写后的查询 / HyDE 假答案会在聊天页助手消息上方显示给用户。Rerank 精排仍使用原查询，不受影响。
                          </p>
                        </div>
                      </div>
                    )}
                  </div>

                  <Separator />

                  {/* ===== 分块设置 ===== */}
                  <div className="space-y-3">
                    <div className="flex items-center gap-2">
                      <Label className="font-medium">分块设置</Label>
                      <Badge variant="outline" className="text-[10px]">入库期</Badge>
                    </div>

                    <div className="space-y-2">
                      <Label className="text-xs">切分策略</Label>
                      <div className="grid grid-cols-4 gap-2">
                        {(["recursive", "markdown", "character", "token"] as const).map((m) => {
                          const active = (retDraft.splitter_strategy ?? retSettings.splitter_strategy) === m;
                          const labels: Record<typeof m, string> = {
                            recursive: "递归字符",
                            markdown: "Markdown",
                            character: "单一分隔",
                            token: "Token",
                          } as Record<typeof m, string>;
                          return (
                            <button
                              key={m}
                              onClick={() => setRetDraft((d) => ({ ...d, splitter_strategy: m }))}
                              className={`rounded-md border px-2 py-2 text-xs transition-colors ${
                                active
                                  ? "border-primary bg-primary/5 text-foreground font-medium"
                                  : "border-input text-muted-foreground hover:border-primary/40"
                              }`}
                            >
                              {labels[m]}
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1.5">
                        <Label className="text-xs">切片大小 (chunk_size)</Label>
                        <Input
                          type="number"
                          value={retDraft.chunk_size ?? retSettings.chunk_size}
                          onChange={(e) => setRetDraft((d) => ({ ...d, chunk_size: parseInt(e.target.value) || 500 }))}
                          min={50}
                          max={4000}
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-xs">重叠 (chunk_overlap)</Label>
                        <Input
                          type="number"
                          value={retDraft.chunk_overlap ?? retSettings.chunk_overlap}
                          onChange={(e) => setRetDraft((d) => ({ ...d, chunk_overlap: parseInt(e.target.value) || 0 }))}
                          min={0}
                          max={2000}
                        />
                      </div>
                    </div>

                    <div className="space-y-1.5">
                      <Label className="text-xs">自定义分隔符（高级，每行一个；空 = 用策略默认）</Label>
                      <textarea
                        value={(retDraft.chunk_separators ?? retSettings.chunk_separators ?? [])
                          .map((s) => s.replace(/\n/g, "\\n"))
                          .join("\n")}
                        onChange={(e) => {
                          const arr = e.target.value
                            .split("\n")
                            .map((s) => s.replace(/\\n/g, "\n"))
                            .filter((s) => s.length > 0);
                          setRetDraft((d) => ({ ...d, chunk_separators: arr.length > 0 ? arr : null }));
                        }}
                        rows={2}
                        placeholder={"例如：\\n\\n\n。\n！"}
                        className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-primary"
                      />
                    </div>

                    <div className="flex items-start gap-2 rounded-md border bg-amber-500/10 px-2 py-1.5">
                      <AlertCircle className="size-3.5 text-amber-600 shrink-0 mt-0.5" />
                      <p className="text-[11px] text-amber-700 dark:text-amber-400 leading-relaxed">
                        分块设置仅对新入库 / 重新入库的文档生效。修改后需对旧文档点「重建向量库」或逐个「重新入库」才能全部生效。
                      </p>
                    </div>
                  </div>

                  <Separator />

                  {/* ===== 生成参数 ===== */}
                  <div className="space-y-3">
                    <div className="flex items-center gap-2">
                      <Label className="font-medium">生成参数</Label>
                      <Badge variant="outline" className="text-[10px]">问答期</Badge>
                    </div>

                    {/* temperature */}
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <Label className="text-xs">温度 (temperature)</Label>
                        <span className="text-xs tabular-nums text-muted-foreground">
                          {(retDraft.gen_temperature ?? retSettings.gen_temperature).toFixed(2)}
                        </span>
                      </div>
                      <Slider
                        value={[(retDraft.gen_temperature ?? retSettings.gen_temperature) * 50]}
                        min={0}
                        max={100}
                        step={5}
                        onValueChange={(v: unknown) => {
                          const num = Array.isArray(v) ? (v[0] as number) : (v as number);
                          setRetDraft((d) => ({ ...d, gen_temperature: num / 50 }));
                        }}
                      />
                      <p className="text-[11px] text-muted-foreground">0 = 最确定，2 = 最发散。请求级 temperature 仍可覆盖。</p>
                    </div>

                    {/* top_p toggle + slider */}
                    <div className="flex items-center justify-between">
                      <div>
                        <Label className="text-sm">启用 top_p（核采样）</Label>
                        <p className="text-[11px] text-muted-foreground mt-0.5">关闭则不显式传，用模型默认</p>
                      </div>
                      <button
                        type="button"
                        role="switch"
                        aria-checked={(retDraft.gen_top_p ?? retSettings.gen_top_p) != null}
                        onClick={() =>
                          setRetDraft((d) => {
                            const cur = d.gen_top_p ?? retSettings.gen_top_p;
                            return { ...d, gen_top_p: cur != null ? null : 1.0 };
                          })
                        }
                        className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border transition-colors ${
                          (retDraft.gen_top_p ?? retSettings.gen_top_p) != null
                            ? "bg-primary border-primary"
                            : "bg-muted border-input"
                        }`}
                      >
                        <span className={`inline-block size-3.5 transform rounded-full bg-white shadow-sm transition-transform ${
                          (retDraft.gen_top_p ?? retSettings.gen_top_p) != null ? "translate-x-4" : "translate-x-0.5"
                        }`} />
                      </button>
                    </div>
                    {(retDraft.gen_top_p ?? retSettings.gen_top_p) != null && (
                      <div className="space-y-2 pl-4 border-l-2 border-primary/20">
                        <div className="flex items-center justify-between">
                          <Label className="text-xs">top_p</Label>
                          <span className="text-xs tabular-nums text-muted-foreground">
                            {((retDraft.gen_top_p ?? retSettings.gen_top_p) ?? 1).toFixed(2)}
                          </span>
                        </div>
                        <Slider
                          value={[((retDraft.gen_top_p ?? retSettings.gen_top_p) ?? 1) * 100]}
                          min={0}
                          max={100}
                          step={5}
                          onValueChange={(v: unknown) => {
                            const num = Array.isArray(v) ? (v[0] as number) : (v as number);
                            setRetDraft((d) => ({ ...d, gen_top_p: num / 100 }));
                          }}
                        />
                      </div>
                    )}

                    {/* max_tokens toggle + input */}
                    <div className="flex items-center justify-between">
                      <div>
                        <Label className="text-sm">自定义 max_tokens</Label>
                        <p className="text-[11px] text-muted-foreground mt-0.5">关闭则用 Provider 自身上限</p>
                      </div>
                      <button
                        type="button"
                        role="switch"
                        aria-checked={(retDraft.gen_max_tokens ?? retSettings.gen_max_tokens) != null}
                        onClick={() =>
                          setRetDraft((d) => {
                            const cur = d.gen_max_tokens ?? retSettings.gen_max_tokens;
                            return { ...d, gen_max_tokens: cur != null ? null : 2048 };
                          })
                        }
                        className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border transition-colors ${
                          (retDraft.gen_max_tokens ?? retSettings.gen_max_tokens) != null
                            ? "bg-primary border-primary"
                            : "bg-muted border-input"
                        }`}
                      >
                        <span className={`inline-block size-3.5 transform rounded-full bg-white shadow-sm transition-transform ${
                          (retDraft.gen_max_tokens ?? retSettings.gen_max_tokens) != null ? "translate-x-4" : "translate-x-0.5"
                        }`} />
                      </button>
                    </div>
                    {(retDraft.gen_max_tokens ?? retSettings.gen_max_tokens) != null && (
                      <div className="space-y-1.5 pl-4 border-l-2 border-primary/20">
                        <Label className="text-xs">max_tokens</Label>
                        <Input
                          type="number"
                          value={(retDraft.gen_max_tokens ?? retSettings.gen_max_tokens) ?? 2048}
                          onChange={(e) => setRetDraft((d) => ({ ...d, gen_max_tokens: parseInt(e.target.value) || 1 }))}
                          min={1}
                          max={131072}
                          className="w-32"
                        />
                      </div>
                    )}

                    {/* penalties */}
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1.5">
                        <Label className="text-xs">presence_penalty</Label>
                        <Input
                          type="number"
                          step={0.1}
                          value={retDraft.gen_presence_penalty ?? retSettings.gen_presence_penalty}
                          onChange={(e) => setRetDraft((d) => ({ ...d, gen_presence_penalty: parseFloat(e.target.value) || 0 }))}
                          min={-2}
                          max={2}
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-xs">frequency_penalty</Label>
                        <Input
                          type="number"
                          step={0.1}
                          value={retDraft.gen_frequency_penalty ?? retSettings.gen_frequency_penalty}
                          onChange={(e) => setRetDraft((d) => ({ ...d, gen_frequency_penalty: parseFloat(e.target.value) || 0 }))}
                          min={-2}
                          max={2}
                        />
                      </div>
                    </div>

                    {/* stop sequences */}
                    <div className="space-y-1.5">
                      <Label className="text-xs">停止序列（每行一个；空 = 不设置）</Label>
                      <textarea
                        value={(retDraft.gen_stop ?? retSettings.gen_stop ?? []).join("\n")}
                        onChange={(e) => {
                          const arr = e.target.value.split("\n").map((s) => s.trim()).filter((s) => s.length > 0);
                          setRetDraft((d) => ({ ...d, gen_stop: arr.length > 0 ? arr : null }));
                        }}
                        rows={2}
                        placeholder={"例如：\n###\nObservation:"}
                        className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-primary"
                      />
                    </div>

                    {/* context length + history */}
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1.5">
                        <Label className="text-xs">上下文最大字符数</Label>
                        <Input
                          type="number"
                          value={retDraft.max_context_length ?? retSettings.max_context_length}
                          onChange={(e) => setRetDraft((d) => ({ ...d, max_context_length: parseInt(e.target.value) || 8000 }))}
                          min={500}
                          max={200000}
                          step={500}
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-xs">多轮历史条数</Label>
                        <Input
                          type="number"
                          value={retDraft.max_history_messages ?? retSettings.max_history_messages}
                          onChange={(e) => setRetDraft((d) => ({ ...d, max_history_messages: parseInt(e.target.value) || 0 }))}
                          min={0}
                          max={100}
                        />
                      </div>
                    </div>
                  </div>

                  <Separator />

                  {/* ===== Prompt 与拒答 ===== */}
                  <div className="space-y-3">
                    <div className="flex items-center gap-2">
                      <Label className="font-medium">Prompt 与拒答</Label>
                      <Badge variant="outline" className="text-[10px]">问答期</Badge>
                    </div>

                    <div className="space-y-1.5">
                      <Label className="text-xs">RAG 系统提示词（留空 = 用内置默认）</Label>
                      <textarea
                        value={retDraft.system_prompt_rag ?? retSettings.system_prompt_rag ?? ""}
                        onChange={(e) => setRetDraft((d) => ({ ...d, system_prompt_rag: e.target.value }))}
                        rows={5}
                        placeholder="留空使用内置 RAG 提示词（含引用规则、防注入、资料不足拒答）"
                        className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-xs leading-relaxed focus:outline-none focus:ring-1 focus:ring-primary"
                      />
                    </div>

                    <div className="space-y-1.5">
                      <Label className="text-xs">直答系统提示词（留空 = 用内置默认）</Label>
                      <textarea
                        value={retDraft.system_prompt_direct ?? retSettings.system_prompt_direct ?? ""}
                        onChange={(e) => setRetDraft((d) => ({ ...d, system_prompt_direct: e.target.value }))}
                        rows={3}
                        placeholder="留空使用内置直答提示词（纯对话场景，无知识库参考）"
                        className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-xs leading-relaxed focus:outline-none focus:ring-1 focus:ring-primary"
                      />
                    </div>

                    <div className="space-y-1.5">
                      <Label className="text-xs">拒答文案（检索无命中时；留空 = 用内置默认）</Label>
                      <textarea
                        value={retDraft.no_answer_text ?? retSettings.no_answer_text ?? ""}
                        onChange={(e) => setRetDraft((d) => ({ ...d, no_answer_text: e.target.value }))}
                        rows={2}
                        placeholder="当前知识库资料不足，无法确定该问题的答案。"
                        className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-xs leading-relaxed focus:outline-none focus:ring-1 focus:ring-primary"
                      />
                    </div>

                    <div className="flex items-center justify-between">
                      <div>
                        <Label className="font-medium">检索无命中时回退直答</Label>
                        <p className="text-[11px] text-muted-foreground mt-0.5">
                          开启后，知识库检索不到相关内容时改用纯 LLM 回答（关闭则严格拒答，更防幻觉）
                        </p>
                      </div>
                      <button
                        type="button"
                        role="switch"
                        aria-checked={retDraft.allow_fallback_to_direct ?? retSettings.allow_fallback_to_direct}
                        onClick={() => setRetDraft((d) => ({ ...d, allow_fallback_to_direct: !(d.allow_fallback_to_direct ?? retSettings!.allow_fallback_to_direct) }))}
                        className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border transition-colors ${
                          (retDraft.allow_fallback_to_direct ?? retSettings.allow_fallback_to_direct)
                            ? "bg-primary border-primary"
                            : "bg-muted border-input"
                        }`}
                      >
                        <span className={`inline-block size-3.5 transform rounded-full bg-white shadow-sm transition-transform ${
                          (retDraft.allow_fallback_to_direct ?? retSettings.allow_fallback_to_direct) ? "translate-x-4" : "translate-x-0.5"
                        }`} />
                      </button>
                    </div>
                  </div>

                  <Separator />

                  {/* Action buttons */}
                  <div className="flex gap-2">
                    <Button onClick={handleSaveRetrieval} disabled={retSaving} className="flex-1">
                      <Save className="size-3.5 mr-1.5" />
                      {retSaving ? "保存中..." : "保存到全局"}
                    </Button>
                    <Button variant="outline" onClick={handleResetRetrieval}>
                      <RotateCcw className="size-3.5 mr-1.5" />
                      重置
                    </Button>
                  </div>

                  {retSettings.updated_at && (
                    <p className="text-[11px] text-muted-foreground text-center">
                      上次保存: {new Date(retSettings.updated_at).toLocaleString("zh-CN")}
                    </p>
                  )}
                </CardContent>
              </Card>

              {/* Right: preview panel */}
              <Card>
                <CardHeader className="pb-4">
                  <CardTitle className="flex items-center gap-2 text-lg">
                    <Search className="size-5 text-blue-600" />
                    检索预览
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex items-center gap-2 rounded-lg border bg-muted/30 px-3 py-1">
                    <Info className="size-3.5 text-muted-foreground shrink-0" />
                    <p className="text-[11px] text-muted-foreground">
                      输入问题后点击「检索」，使用左侧当前参数运行一次混合检索（不调 LLM），查看召回片段的排名与分数。
                    </p>
                  </div>

                  <div className="flex gap-2">
                    <Input
                      value={previewQuery}
                      onChange={(e) => setPreviewQuery(e.target.value)}
                      placeholder="输入测试问题..."
                      onKeyDown={(e) => { if (e.key === "Enter") handlePreview(); }}
                    />
                    <Button onClick={handlePreview} disabled={previewLoading || !previewQuery.trim()}>
                      {previewLoading ? <RefreshCw className="size-3.5 animate-spin" /> : <Search className="size-3.5" />}
                    </Button>
                  </div>

                  {previewUsedMode && (
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary" className="text-[10px]">策略: {previewUsedMode}</Badge>
                      <Badge variant="secondary" className="text-[10px]">命中 {previewItems.length} 条</Badge>
                    </div>
                  )}

                  {previewItems.length > 0 && (
                    <div className="space-y-2 max-h-[520px] overflow-y-auto pr-1">
                      {previewItems.map((item, idx) => (
                        <motion.div
                          key={item.id}
                          initial={{ opacity: 0, y: 8 }}
                          animate={{ opacity: 1, y: 0 }}
                          transition={{ delay: idx * 0.04 }}
                          className="rounded-lg border bg-card p-3 space-y-2"
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex items-center gap-2 min-w-0">
                              <span className="flex items-center justify-center size-6 rounded-full bg-primary/10 text-primary text-xs font-bold shrink-0">
                                {idx + 1}
                              </span>
                              <Badge variant="outline" className="text-[10px] truncate max-w-[180px]">
                                {item.source.split(/[/\\]/).pop()}
                              </Badge>
                            </div>
                            <span className="text-xs font-mono text-muted-foreground shrink-0">
                              融合 {item.fused_score.toFixed(4)}
                            </span>
                          </div>

                          <p className="text-xs text-muted-foreground line-clamp-3 whitespace-pre-wrap leading-relaxed">
                            {item.content}
                          </p>

                          <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
                            {item.sem_rank != null && (
                              <span>语义 #{item.sem_rank} ({item.sem_norm?.toFixed(3)})</span>
                            )}
                            {item.bm25_rank != null && (
                              <span>BM25 #{item.bm25_rank} ({item.bm25_norm?.toFixed(3)})</span>
                            )}
                            {item.rerank_score != null && (
                              <span className="text-amber-600">精排 {item.rerank_score.toFixed(1)}分 (原#{item.original_rank})</span>
                            )}
                          </div>
                        </motion.div>
                      ))}
                    </div>
                  )}

                  {!previewLoading && previewItems.length === 0 && previewUsedMode && (
                    <p className="text-center py-6 text-muted-foreground text-sm">未检索到任何片段</p>
                  )}
                </CardContent>
              </Card>
            </div>
          )}
        </TabsContent>
      </Tabs>

      {/* Skill create/edit dialog */}
      <Dialog open={skillDialogOpen} onOpenChange={setSkillDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {editingSkill ? "编辑技能" : "新建技能"}
            </DialogTitle>
            <DialogClose />
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>名称 *</Label>
              <Input
                value={skillForm.name}
                onChange={(e) => setSkillForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="如：财务分析"
              />
            </div>
            <div className="space-y-2">
              <Label>图标</Label>
              <Input
                value={skillForm.icon}
                onChange={(e) => setSkillForm((f) => ({ ...f, icon: e.target.value }))}
                placeholder="如：📊"
              />
            </div>
            <div className="space-y-2">
              <Label>描述</Label>
              <Input
                value={skillForm.description}
                onChange={(e) => setSkillForm((f) => ({ ...f, description: e.target.value }))}
                placeholder="技能的简要描述"
              />
            </div>
            <div className="space-y-2">
              <Label>系统提示词 *</Label>
              <Textarea
                value={skillForm.system_prompt}
                onChange={(e) => setSkillForm((f) => ({ ...f, system_prompt: e.target.value }))}
                placeholder="LLM 系统角色提示词"
                rows={5}
                className="resize-y"
              />
            </div>
            <div className="space-y-2">
              <Label>规则</Label>
              <Textarea
                value={skillForm.rules}
                onChange={(e) => setSkillForm((f) => ({ ...f, rules: e.target.value }))}
                placeholder="可选的规则（关键词=回答 格式，每行一条）"
                rows={3}
                className="resize-y"
              />
            </div>
            <div className="space-y-2">
              <Label>自动识别关键词</Label>
              <Textarea
                value={skillForm.auto_detect_patterns}
                onChange={(e) =>
                  setSkillForm((f) => ({ ...f, auto_detect_patterns: e.target.value }))
                }
                placeholder="文档上传时若文件名/内容命中以下任一关键词，将提示切换到本技能。多个用英文/中文逗号或换行分隔。例如：财务, 报表, 资产负债"
                rows={2}
                className="resize-y"
              />
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setSkillDialogOpen(false)}>
                取消
              </Button>
              <Button onClick={handleSaveSkill} disabled={skillSaving}>
                {skillSaving ? "保存中..." : "保存"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Provider create/edit dialog */}
      <Dialog open={providerDialogOpen} onOpenChange={setProviderDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {editingProvider ? "编辑模型配置" : "新增模型配置"}
            </DialogTitle>
            <DialogClose />
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>模型类型 *</Label>
              <div className="grid grid-cols-3 gap-2">
                {(["llm", "embedding", "ocr"] as ModelType[]).map((t) => {
                  const meta = MODEL_TYPE_META[t];
                  const TypeIcon = meta.icon;
                  const active = providerForm.model_type === t;
                  return (
                    <button
                      key={t}
                      type="button"
                      onClick={() => {
                        if (editingProvider) {
                          setProviderForm((f) => ({ ...f, model_type: t }));
                        } else {
                          applyTypeDefaults(t);
                        }
                      }}
                      className={`flex items-center justify-center gap-1.5 rounded-md border px-2 py-2 text-xs transition-colors ${
                        active
                          ? "border-primary bg-primary/5 text-foreground"
                          : "border-input bg-transparent text-muted-foreground hover:border-primary/40"
                      }`}
                    >
                      <TypeIcon className={`size-3.5 ${meta.color}`} />
                      <span>{meta.label.split(" ")[0]}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>名称 *</Label>
                <Input
                  value={providerForm.name}
                  onChange={(e) => setProviderForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder={MODEL_TYPE_META[providerForm.model_type].placeholder.name}
                />
              </div>
              <div className="space-y-2">
                <Label>部署形态</Label>
                <select
                  className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  value={providerForm.provider_type}
                  onChange={(e) => setProviderForm((f) => ({ ...f, provider_type: e.target.value }))}
                >
                  <option value="remote">在线 API</option>
                  <option value="local">本地中转</option>
                </select>
              </div>
            </div>
            <div className="space-y-2">
              <Label>API 地址 *</Label>
              <Input
                value={providerForm.base_url}
                onChange={(e) => setProviderForm((f) => ({ ...f, base_url: e.target.value }))}
                placeholder={MODEL_TYPE_META[providerForm.model_type].placeholder.base_url}
              />
            </div>
            <div className="space-y-2">
              <Label>API Key {editingProvider ? "（留空不修改）" : "*"}</Label>
              <Input
                type="password"
                value={providerForm.api_key}
                onChange={(e) => setProviderForm((f) => ({ ...f, api_key: e.target.value }))}
                placeholder={editingProvider ? "留空保持原值" : "sk-..."}
              />
            </div>
            <div className="space-y-2">
              <Label>模型名称 *</Label>
              <Input
                value={providerForm.model_name}
                onChange={(e) => setProviderForm((f) => ({ ...f, model_name: e.target.value }))}
                placeholder={MODEL_TYPE_META[providerForm.model_type].placeholder.model_name}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>max_tokens</Label>
                <Input
                  type="number"
                  value={providerForm.max_tokens}
                  onChange={(e) => setProviderForm((f) => ({ ...f, max_tokens: e.target.value }))}
                />
              </div>
              <div className="space-y-2">
                <Label>超时时间（秒）</Label>
                <Input
                  type="number"
                  value={providerForm.timeout_seconds}
                  onChange={(e) => setProviderForm((f) => ({ ...f, timeout_seconds: e.target.value }))}
                />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="is_default"
                checked={providerForm.is_default}
                onChange={(e) => setProviderForm((f) => ({ ...f, is_default: e.target.checked }))}
                className="rounded border-input"
              />
              <Label htmlFor="is_default" className="text-sm font-normal cursor-pointer">
                设为该类型的默认模型
              </Label>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setProviderDialogOpen(false)}>
                取消
              </Button>
              <Button onClick={handleSaveProvider} disabled={providerSaving}>
                {providerSaving ? "保存中..." : "保存"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Toast */}
      <AnimatePresence>
        {toast && (
          <motion.div
            initial={{ opacity: 0, y: 40 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 40 }}
            transition={{ type: "spring", stiffness: 400, damping: 30 }}
            className={`fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-lg px-4 py-3 text-sm font-medium shadow-lg ${
              toast.type === "success"
                ? "bg-green-600 text-white"
                : "bg-destructive text-destructive-foreground"
            }`}
          >
            {toast.type === "success" ? (
              <CheckCircle2 className="size-4" />
            ) : (
              <AlertCircle className="size-4" />
            )}
            {toast.text}
            <button onClick={() => setToast(null)} className="ml-2 opacity-70 hover:opacity-100">
              <X className="size-3.5" />
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
