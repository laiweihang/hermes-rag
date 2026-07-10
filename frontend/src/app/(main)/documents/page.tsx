"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Upload,
  FileText,
  Trash2,
  RefreshCw,
  Eye,
  X,
  AlertCircle,
  CheckCircle2,
  Sparkles,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { get, post, del } from "@/lib/api";
import apiClient from "@/lib/api";
import { useChatContext } from "@/lib/chat-context";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogClose,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import Thinking from "@/components/thinking";
import { ProviderQuickPicker } from "@/components/provider-quick-picker";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface DocumentInfo {
  name: string;
  chunk_count: number;
  status: "queued" | "processing" | "ready" | "failed";
  error_message?: string | null;
}

interface DocumentListResponse {
  documents: DocumentInfo[];
}

interface DocumentPreviewResponse {
  filename: string;
  content: string;
  total_pages: number | null;
  previewed_pages: number | null;
  total_chars: number;
  previewed_chars: number;
}

interface UploadResponse {
  filename: string;
  status: string;
  message: string;
}

interface SkillDetectResponse {
  suggested_skill_id: number | null;
  skill_name: string | null;
}

/* ------------------------------------------------------------------ */
/*  Module-level init fetcher (avoids React Compiler dep injection)    */
/* ------------------------------------------------------------------ */

async function initLoadDocuments(
  setDocuments: React.Dispatch<React.SetStateAction<DocumentInfo[]>>,
  setLoading: React.Dispatch<React.SetStateAction<boolean>>,
  onListLoaded?: (count: number) => void,
) {
  try {
    const data = await get<DocumentListResponse>("/api/documents");
    const list = data.documents ?? [];
    setDocuments(list);
    onListLoaded?.(list.length);
  } catch {
    /* silent on init */
  } finally {
    setLoading(false);
  }
}

/* ------------------------------------------------------------------ */
/*  Page                                                               */
/* ------------------------------------------------------------------ */

export default function DocumentsPage() {
  const router = useRouter();
  const { setActiveSkillId, syncDocumentCountFromLength } = useChatContext();

  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [toast, setToast] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const [previewDoc, setPreviewDoc] = useState<DocumentPreviewResponse | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [reingestingName, setReingestingName] = useState<string | null>(null);

  const [skillSuggestion, setSkillSuggestion] = useState<{ id: number; name: string } | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const showToast = useCallback((type: "success" | "error", text: string) => {
    setToast({ type, text });
    setTimeout(() => setToast(null), 3000);
  }, []);

  const fetchDocuments = useCallback(async () => {
    try {
      const data = await get<DocumentListResponse>("/api/documents");
      const list = data.documents ?? [];
      setDocuments(list);
      syncDocumentCountFromLength(list.length);
    } catch {
      showToast("error", "获取文档列表失败");
    } finally {
      setLoading(false);
    }
  }, [showToast, syncDocumentCountFromLength]);

  useEffect(() => {
    void initLoadDocuments(setDocuments, setLoading, syncDocumentCountFromLength);
  }, [syncDocumentCountFromLength]);

  useEffect(() => {
    if (!documents.some((doc) => doc.status === "queued" || doc.status === "processing")) return;
    const timer = window.setInterval(() => void fetchDocuments(), 2000);
    return () => window.clearInterval(timer);
  }, [documents, fetchDocuments]);

  /* ---- upload ---- */
  async function handleUpload(files: FileList | File[]) {
    if (!files.length) return;
    setUploading(true);
    const uploadedNames: string[] = [];
    for (const file of Array.from(files)) {
      try {
        const formData = new FormData();
        formData.append("file", file);
        await apiClient.post<UploadResponse>("/upload", formData, {
          headers: { "Content-Type": "multipart/form-data" },
        });
        uploadedNames.push(file.name);
      } catch (err) {
        const e = err as { response?: { data?: { detail?: string } }; message?: string };
        const detail = e?.response?.data?.detail || e?.message || "未知错误";
        showToast("error", `上传 ${file.name} 失败: ${detail}`);
      }
    }
    if (uploadedNames.length > 0) {
      showToast("success", `${uploadedNames.length} 个文件已加入后台入库队列`);
      await fetchDocuments();
      tryDetectSkill(uploadedNames);
    }
    setUploading(false);
  }

  async function tryDetectSkill(filenames: string[]) {
    try {
      const res = await post<SkillDetectResponse>("/api/skills/detect", {
        filename: filenames.join(" "),
      });
      if (res.suggested_skill_id != null && res.skill_name) {
        setSkillSuggestion({ id: res.suggested_skill_id, name: res.skill_name });
      }
    } catch {
      /* silent */
    }
  }

  function handleAcceptSkill() {
    if (!skillSuggestion) return;
    setActiveSkillId(skillSuggestion.id);
    setSkillSuggestion(null);
    router.push("/");
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    if (e.target.files) handleUpload(e.target.files);
    e.target.value = "";
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files) handleUpload(e.dataTransfer.files);
  }

  /* ---- delete ---- */
  async function handleDelete(name: string) {
    try {
      await del(`/api/documents/${encodeURIComponent(name)}`);
      showToast("success", `已删除 ${name}`);
      await fetchDocuments();
    } catch {
      showToast("error", `删除 ${name} 失败`);
    }
  }

  /* ---- reingest ---- */
  async function handleReingest(name: string) {
    setReingestingName(name);
    try {
      await post(`/api/documents/${encodeURIComponent(name)}/reingest`);
      showToast("success", `${name} 已加入重新入库队列`);
      await fetchDocuments();
    } catch {
      showToast("error", `重新入库 ${name} 失败`);
    } finally {
      setReingestingName(null);
    }
  }

  /* ---- preview ---- */
  async function handlePreview(filename: string) {
    setPreviewLoading(true);
    setPreviewOpen(true);
    try {
      const data = await get<DocumentPreviewResponse>(
        `/api/documents/${encodeURIComponent(filename)}/preview`
      );
      setPreviewDoc(data);
    } catch {
      showToast("error", "预览失败");
      setPreviewOpen(false);
    } finally {
      setPreviewLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h1 className="text-2xl font-bold text-foreground">文档管理</h1>
        <Badge variant="secondary">{documents.length} 个文档</Badge>
      </div>

      {/* 模型状态条：上传时所用的 OCR / 嵌入模型 */}
      <div className="flex items-center gap-1 rounded-lg border border-border bg-muted/40 px-2 py-1.5 flex-wrap text-xs text-muted-foreground">
        <span className="px-1.5">入库使用：</span>
        <ProviderQuickPicker type="ocr" mode="default" />
        <span className="h-4 w-px bg-border mx-1" />
        <ProviderQuickPicker type="embedding" mode="default" />
        <span className="ml-auto text-[11px] px-1.5">
          切换为默认模型仅管理员可操作；上传/重新入库时即时生效。
        </span>
      </div>

      {/* Upload zone */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ type: "spring", stiffness: 300, damping: 30 }}
      >
        <div
          className={`relative flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-10 transition-colors ${
            dragOver
              ? "border-primary bg-primary/5"
              : "border-border hover:border-primary/50"
          }`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
        >
          <Upload className="size-10 text-muted-foreground mb-3" />
          <p className="text-sm text-muted-foreground mb-1">
            拖拽文件到此处，或点击选择文件
          </p>
          <p className="text-xs text-muted-foreground mb-1">
            支持 PDF、Word（DOCX/DOC）、Excel（XLSX）、PowerPoint（PPTX）、
            HTML、Markdown、TXT、CSV、JSON
          </p>
          <p className="text-xs text-muted-foreground mb-4">
            以及图片（PNG/JPG/BMP/TIFF/WebP，自动 OCR）。
            DOC 文件需安装 LibreOffice 才能自动转换。
          </p>
          <Button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
          >
            {uploading ? <><RefreshCw className="size-4 mr-2 animate-spin" />上传中...</> : "选择文件"}
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".pdf,.docx,.doc,.txt,.md,.markdown,.html,.htm,.xlsx,.pptx,.csv,.json,.png,.jpg,.jpeg,.bmp,.tiff,.tif,.webp"
            className="hidden"
            onChange={handleFileChange}
          />
        </div>
      </motion.div>

      {/* Document list */}
      {loading ? (
        <div className="flex justify-center py-12"><Thinking /></div>
      ) : documents.length === 0 ? (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ type: "spring", stiffness: 260, damping: 24 }}
          className="text-center py-12 text-muted-foreground"
        >
          <FileText className="size-12 mx-auto mb-3 opacity-40" />
          <p>暂无文档，上传文件开始使用</p>
        </motion.div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <AnimatePresence mode="popLayout">
            {documents.map((doc) => (
              <motion.div
                key={doc.name}
                layout
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95 }}
                transition={{ type: "spring", stiffness: 300, damping: 25 }}
              >
                <Card className="group">
                  <CardHeader className="pb-2">
                    <CardTitle className="flex items-start gap-2 text-sm font-medium">
                      <FileText className="size-4 mt-0.5 shrink-0 text-primary" />
                      <span className="flex-1 break-all line-clamp-2">{doc.name}</span>
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="flex flex-wrap gap-2">
                      <Badge variant="outline">{doc.chunk_count} 个片段</Badge>
                      <Badge variant={doc.status === "failed" ? "destructive" : "secondary"}>
                        {doc.status === "queued" && "等待入库"}
                        {doc.status === "processing" && "正在入库"}
                        {doc.status === "ready" && "已就绪"}
                        {doc.status === "failed" && "入库失败"}
                      </Badge>
                    </div>
                    {doc.error_message && (
                      <p className="text-xs text-destructive line-clamp-3">{doc.error_message}</p>
                    )}
                    <div className="flex gap-1.5">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handlePreview(doc.name)}
                      >
                        <Eye className="size-3.5 mr-1" />预览
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleReingest(doc.name)}
                        disabled={reingestingName === doc.name || doc.status === "queued" || doc.status === "processing"}
                      >
                        <RefreshCw className={`size-3.5 mr-1 ${reingestingName === doc.name ? "animate-spin" : ""}`} />
                        重新入库
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-destructive hover:text-destructive"
                        onClick={() => handleDelete(doc.name)}
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

      {/* Preview dialog */}
      <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
        <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Eye className="size-4" />
              {previewDoc?.filename ?? "文档预览"}
            </DialogTitle>
            <DialogClose />
          </DialogHeader>
          {previewLoading ? (
            <div className="flex justify-center py-8"><Thinking /></div>
          ) : previewDoc ? (
            <div className="space-y-3 flex-1 min-h-0">
              <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                <span>总字符数: {previewDoc.total_chars}</span>
                {previewDoc.total_pages != null && (
                  <span>总页数: {previewDoc.total_pages}</span>
                )}
                <span>预览字符: {previewDoc.previewed_chars}</span>
              </div>
              <ScrollArea className="flex-1 rounded-md border p-4 max-h-[50vh]">
                <pre className="whitespace-pre-wrap text-sm leading-relaxed">
                  {previewDoc.content}
                </pre>
              </ScrollArea>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>

      {/* Skill suggestion banner */}
      <AnimatePresence>
        {skillSuggestion && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
            transition={{ type: "spring", stiffness: 300, damping: 25 }}
            className="fixed top-20 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 rounded-xl border border-primary/30 bg-primary/5 px-5 py-3 shadow-lg backdrop-blur"
          >
            <Sparkles className="size-5 text-primary shrink-0" />
            <span className="text-sm">
              检测到文档匹配技能「<strong>{skillSuggestion.name}</strong>」，是否切换？
            </span>
            <Button size="sm" onClick={handleAcceptSkill}>
              切换并开始对话
            </Button>
            <button
              className="text-muted-foreground hover:text-foreground"
              onClick={() => setSkillSuggestion(null)}
            >
              <X className="size-4" />
            </button>
          </motion.div>
        )}
      </AnimatePresence>

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
