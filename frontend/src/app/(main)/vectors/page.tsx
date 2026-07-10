"use client";

import { useCallback, useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Search,
  Database,
  Trash2,
  Pencil,
  ChevronLeft,
  ChevronRight,
  X,
  AlertCircle,
  CheckCircle2,
  FileText,
} from "lucide-react";
import { get, put, del } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogClose,
} from "@/components/ui/dialog";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";
import Thinking from "@/components/thinking";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface ChunkOut {
  id: string;
  content: string;
  metadata: Record<string, unknown>;
}

interface ChunkListResponse {
  chunks: ChunkOut[];
  total: number;
}

interface ChunkSearchResult {
  id: string;
  content: string;
  metadata: Record<string, unknown>;
  distance: number;
}

interface ChunkSearchResponse {
  results: ChunkSearchResult[];
}

interface DocumentInfo {
  name: string;
  chunk_count: number;
}

interface DocumentListResponse {
  documents: DocumentInfo[];
}

/* ------------------------------------------------------------------ */
/*  Module-level chunk loader (avoids React Compiler dep injection)    */
/* ------------------------------------------------------------------ */

const PAGE_SIZE = 20;

async function doLoadChunks(
  offset: number,
  sourceFilter: string,
  setChunks: React.Dispatch<React.SetStateAction<ChunkOut[]>>,
  setTotal: React.Dispatch<React.SetStateAction<number>>,
  setLoading: React.Dispatch<React.SetStateAction<boolean>>,
) {
  setLoading(true);
  try {
    const params = new URLSearchParams({
      offset: String(offset),
      limit: String(PAGE_SIZE),
    });
    if (sourceFilter) params.set("source", sourceFilter);
    const data = await get<ChunkListResponse>(`/api/chunks?${params}`);
    setChunks(data.chunks ?? []);
    setTotal(data.total);
  } catch {
    /* silent on auto-load */
  } finally {
    setLoading(false);
  }
}

/* ------------------------------------------------------------------ */
/*  Page                                                               */
/* ------------------------------------------------------------------ */

export default function VectorsPage() {
  const [chunks, setChunks] = useState<ChunkOut[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [sourceFilter, setSourceFilter] = useState<string>("");
  const [sources, setSources] = useState<string[]>([]);

  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<ChunkSearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);

  const [editChunk, setEditChunk] = useState<ChunkOut | null>(null);
  const [editContent, setEditContent] = useState("");
  const [editOpen, setEditOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  const [toast, setToast] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const showToast = useCallback((type: "success" | "error", text: string) => {
    setToast({ type, text });
    setTimeout(() => setToast(null), 3000);
  }, []);

  /* ---- fetch sources ---- */
  useEffect(() => {
    (async () => {
      try {
        const data = await get<DocumentListResponse>("/api/documents");
        setSources((data.documents ?? []).map((d) => d.name));
      } catch { /* silent */ }
    })();
  }, []);

  /* ---- fetch chunks ---- */
  async function fetchChunks() {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        offset: String(offset),
        limit: String(PAGE_SIZE),
      });
      if (sourceFilter) params.set("source", sourceFilter);
      const data = await get<ChunkListResponse>(`/api/chunks?${params}`);
      setChunks(data.chunks ?? []);
      setTotal(data.total);
    } catch {
      showToast("error", "获取片段列表失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setSearchResults(null);
    doLoadChunks(offset, sourceFilter, setChunks, setTotal, setLoading);
  }, [offset, sourceFilter]);

  /* ---- search ---- */
  async function handleSearch() {
    const q = searchQuery.trim();
    if (!q) {
      setSearchResults(null);
      return;
    }
    setSearching(true);
    try {
      const data = await get<ChunkSearchResponse>(
        `/api/chunks/search?q=${encodeURIComponent(q)}&top_k=10`
      );
      setSearchResults(data.results ?? []);
    } catch {
      showToast("error", "搜索失败");
    } finally {
      setSearching(false);
    }
  }

  function clearSearch() {
    setSearchQuery("");
    setSearchResults(null);
  }

  /* ---- edit ---- */
  function openEdit(chunk: ChunkOut) {
    setEditChunk(chunk);
    setEditContent(chunk.content);
    setEditOpen(true);
  }

  async function handleSaveEdit() {
    if (!editChunk) return;
    setSaving(true);
    try {
      await put(`/api/chunks/${encodeURIComponent(editChunk.id)}`, {
        content: editContent,
      });
      showToast("success", "片段已更新");
      setEditOpen(false);
      if (searchResults) {
        setSearchResults((prev) =>
          prev
            ? prev.map((r) =>
                r.id === editChunk.id ? { ...r, content: editContent } : r
              )
            : prev
        );
      }
      fetchChunks();
    } catch {
      showToast("error", "更新失败");
    } finally {
      setSaving(false);
    }
  }

  /* ---- delete ---- */
  async function handleDelete(id: string) {
    try {
      await del(`/api/chunks/${encodeURIComponent(id)}`);
      showToast("success", "片段已删除");
      if (searchResults) {
        setSearchResults((prev) => prev?.filter((r) => r.id !== id) ?? null);
      }
      fetchChunks();
    } catch {
      showToast("error", "删除失败");
    }
  }

  /* ---- pagination ---- */
  const totalPages = Math.ceil(total / PAGE_SIZE);
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  /* ---- render chunk card ---- */
  function ChunkCard({
    id,
    content,
    metadata,
    extra,
  }: {
    id: string;
    content: string;
    metadata: Record<string, unknown>;
    extra?: React.ReactNode;
  }) {
    const source = typeof metadata.source === "string" ? metadata.source : "";
    return (
      <motion.div
        layout
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -8 }}
        transition={{ type: "spring", stiffness: 300, damping: 25 }}
      >
        <Card>
          <CardContent className="py-3 space-y-2">
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2 flex-wrap">
                {source && (
                  <Badge variant="outline" className="text-xs">
                    <FileText className="size-3 mr-1" />{source}
                  </Badge>
                )}
                {extra}
              </div>
              <div className="flex gap-1 shrink-0">
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7"
                  onClick={() => openEdit({ id, content, metadata })}
                >
                  <Pencil className="size-3.5" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7 text-destructive hover:text-destructive"
                  onClick={() => handleDelete(id)}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            </div>
            <p className="text-sm leading-relaxed whitespace-pre-wrap line-clamp-4">
              {content}
            </p>
            <p className="text-xs text-muted-foreground font-mono truncate">
              ID: {id}
            </p>
          </CardContent>
        </Card>
      </motion.div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-foreground">向量片段</h1>
        <Badge variant="secondary">{total} 个片段</Badge>
      </div>

      {/* Search bar */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ type: "spring", stiffness: 300, damping: 30 }}
        className="flex gap-2"
      >
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            placeholder="输入关键词进行向量相似度检索..."
            className="pl-9"
          />
          {searchQuery && (
            <button
              className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              onClick={clearSearch}
            >
              <X className="size-4" />
            </button>
          )}
        </div>
        <Button onClick={handleSearch} disabled={searching}>
          {searching ? "检索中..." : "检索"}
        </Button>
      </motion.div>

      {/* Source filter (only when not searching) */}
      {!searchResults && sources.length > 0 && (
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">按来源筛选:</span>
          <Select
            value={sourceFilter}
            onValueChange={(v) => { setSourceFilter(!v || v === "__all__" ? "" : v); setOffset(0); }}
          >
            <SelectTrigger className="w-[220px]">
              <SelectValue placeholder="全部来源" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">全部来源</SelectItem>
              {sources.map((s) => (
                <SelectItem key={s} value={s}>{s}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {/* Search results */}
      {searchResults !== null ? (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              检索到 {searchResults.length} 个相关片段
            </p>
            <Button variant="ghost" size="sm" onClick={clearSearch}>
              返回列表
            </Button>
          </div>
          {searching ? (
            <div className="flex justify-center py-8"><Thinking /></div>
          ) : searchResults.length === 0 ? (
            <p className="text-center py-8 text-muted-foreground">无匹配结果</p>
          ) : (
            <div className="space-y-3">
              <AnimatePresence mode="popLayout">
                {searchResults.map((r) => (
                  <ChunkCard
                    key={r.id}
                    id={r.id}
                    content={r.content}
                    metadata={r.metadata}
                    extra={
                      <Badge variant="secondary" className="text-xs">
                        距离: {r.distance.toFixed(4)}
                      </Badge>
                    }
                  />
                ))}
              </AnimatePresence>
            </div>
          )}
        </div>
      ) : loading ? (
        <div className="flex justify-center py-12"><Thinking /></div>
      ) : chunks.length === 0 ? (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ type: "spring", stiffness: 260, damping: 24 }}
          className="text-center py-12 text-muted-foreground"
        >
          <Database className="size-12 mx-auto mb-3 opacity-40" />
          <p>暂无向量片段</p>
        </motion.div>
      ) : (
        <>
          <div className="space-y-3">
            <AnimatePresence mode="popLayout">
              {chunks.map((c) => (
                <ChunkCard
                  key={c.id}
                  id={c.id}
                  content={c.content}
                  metadata={c.metadata}
                />
              ))}
            </AnimatePresence>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-3 pt-2">
              <Button
                variant="outline"
                size="sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                <ChevronLeft className="size-4 mr-1" />上一页
              </Button>
              <span className="text-sm text-muted-foreground">
                第 {currentPage} / {totalPages} 页
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                下一页<ChevronRight className="size-4 ml-1" />
              </Button>
            </div>
          )}
        </>
      )}

      {/* Edit dialog */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Pencil className="size-4" />编辑片段
            </DialogTitle>
            <DialogClose />
          </DialogHeader>
          <div className="space-y-4">
            {editChunk && (
              <p className="text-xs text-muted-foreground font-mono truncate">
                ID: {editChunk.id}
              </p>
            )}
            <Textarea
              value={editContent}
              onChange={(e) => setEditContent(e.target.value)}
              rows={10}
              className="resize-y"
            />
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setEditOpen(false)}>
                取消
              </Button>
              <Button
                onClick={handleSaveEdit}
                disabled={saving || !editContent.trim()}
              >
                {saving ? "保存中..." : "保存"}
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
