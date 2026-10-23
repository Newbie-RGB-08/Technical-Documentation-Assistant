/**
 * 上传列表/引擎状态 hook（PRD 7.11）。
 *
 * - 页面挂载时拉取一次，之后每 1 秒轮询列表；卸载/禁用时清理定时器。
 * - 上传使用 XHR 获取进度；上传/重试/删除后立即刷新列表。
 * - 轮询失败保留列表，只更新错误提示，避免界面闪空。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteUpload,
  getUploadEngines,
  getUploads,
  reindexUpload,
  reparseUpload,
  uploadFiles,
} from "../api/client";
import type {
  EngineInfo,
  UploadCreateResponse,
  UploadFile,
  UploadOptions,
} from "../types/upload";

const DEFAULT_POLL_MS = 1000;

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

export interface UseUploadsOptions {
  pollIntervalMs?: number;
  enabled?: boolean;
  pageSize?: number;
}

export function useUploads({
  pollIntervalMs = DEFAULT_POLL_MS,
  enabled = true,
  pageSize = 100,
}: UseUploadsOptions = {}) {
  const [items, setItems] = useState<UploadFile[]>([]);
  const [total, setTotal] = useState(0);
  const [engines, setEngines] = useState<EngineInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [engineError, setEngineError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [actionBusy, setActionBusy] = useState<string | null>(null);

  const mountedRef = useRef(true);
  const listInFlightRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const load = useCallback(
    async (silent = false) => {
      if (listInFlightRef.current) return;
      listInFlightRef.current = true;
      if (!silent) setLoading(true);
      try {
        const result = await getUploads({ limit: pageSize });
        if (!mountedRef.current) return;
        setItems(result.items);
        setTotal(result.total);
        if (!silent) setError(null);
      } catch (reason) {
        if (mountedRef.current) setError(errorMessage(reason));
      } finally {
        listInFlightRef.current = false;
        if (mountedRef.current && !silent) setLoading(false);
      }
    },
    [pageSize],
  );

  const loadEngines = useCallback(async () => {
    try {
      const result = await getUploadEngines();
      if (mountedRef.current) {
        setEngines(result.engines ?? []);
        setEngineError(null);
      }
    } catch (reason) {
      if (mountedRef.current) setEngineError(errorMessage(reason));
    }
  }, []);

  useEffect(() => {
    void load(false);
    void loadEngines();
  }, [load, loadEngines]);

  useEffect(() => {
    if (!enabled) return;
    const timer = window.setInterval(() => {
      void load(true);
    }, pollIntervalMs);
    return () => window.clearInterval(timer);
  }, [enabled, pollIntervalMs, load]);

  const upload = useCallback(
    async (files: File[], options: UploadOptions): Promise<UploadCreateResponse | null> => {
      if (files.length === 0) return null;
      setUploading(true);
      setUploadProgress(0);
      setError(null);
      try {
        const result = await uploadFiles(files, options, {
          onProgress: (percent) => {
            if (mountedRef.current) setUploadProgress(percent);
          },
        });
        if (mountedRef.current) setUploadProgress(100);
        await load(true);
        return result;
      } catch (reason) {
        if (mountedRef.current) setError(errorMessage(reason));
        return null;
      } finally {
        if (mountedRef.current) setUploading(false);
      }
    },
    [load],
  );

  const runAction = useCallback(
    async (uploadId: string, action: () => Promise<unknown>): Promise<boolean> => {
      setActionBusy(uploadId);
      setError(null);
      try {
        await action();
        await load(true);
        return true;
      } catch (reason) {
        if (mountedRef.current) setError(errorMessage(reason));
        return false;
      } finally {
        if (mountedRef.current) setActionBusy(null);
      }
    },
    [load],
  );

  const reparse = useCallback(
    (uploadId: string) => runAction(uploadId, () => reparseUpload(uploadId)),
    [runAction],
  );

  const reindex = useCallback(
    (uploadId: string) => runAction(uploadId, () => reindexUpload(uploadId)),
    [runAction],
  );

  const remove = useCallback(
    (uploadId: string) => runAction(uploadId, () => deleteUpload(uploadId)),
    [runAction],
  );

  const refresh = useCallback(async () => {
    await Promise.all([load(false), loadEngines()]);
  }, [load, loadEngines]);

  return {
    items,
    total,
    engines,
    loading,
    error,
    engineError,
    uploading,
    uploadProgress,
    actionBusy,
    upload,
    reparse,
    reindex,
    remove,
    refresh,
    load,
    loadEngines,
    clearError: () => setError(null),
  };
}
