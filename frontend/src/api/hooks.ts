import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, rawFetch, toApiError, unwrap, ApiRequestError } from "./client";
import type { ColumnMappingEntry, FileInfo, Item, Job, JobStatus } from "./types";

/** Статусы, в которых задача ещё идёт: пока так — опрашиваем её раз в 1,5 с (как просит контракт). */
export const ACTIVE_STATUSES: JobStatus[] = ["queued", "parsing", "processing"];
export const isActive = (s?: JobStatus) => !!s && ACTIVE_STATUSES.includes(s);

export const keys = {
  config: ["config"] as const,
  health: ["health"] as const,
  jobs: (page: number) => ["jobs", page] as const,
  job: (id: string) => ["job", id] as const,
  items: (id: string, params: ItemsParams) => ["items", id, params] as const,
  item: (id: string) => ["item", id] as const,
  catalogSearch: (q: string, v?: string) => ["catalog-search", q, v] as const,
  quality: ["quality"] as const,
  stats: ["stats"] as const,
};

// ---------------------------------------------------------------- система

export function useConfig() {
  return useQuery({
    queryKey: keys.config,
    queryFn: () => unwrap(api.GET("/config")),
    staleTime: Infinity,
  });
}

export function useHealth() {
  return useQuery({
    queryKey: keys.health,
    queryFn: () => unwrap(api.GET("/health")),
    // Пока система не готова — проверяем чаще, потом раз в 30 с.
    refetchInterval: (q) => (q.state.data?.ready === false ? 3000 : 30000),
    retry: false,
  });
}

// ---------------------------------------------------------------- файлы и задачи

export function useUploadFile() {
  return useMutation({
    mutationFn: async (file: File): Promise<FileInfo> => {
      const fd = new FormData();
      fd.append("file", file);
      let res: Response;
      try {
        res = await rawFetch("/files", { method: "POST", body: fd });
      } catch {
        throw new ApiRequestError(0, "network_error", "Нет связи с сервером.");
      }
      const body = await res.json().catch(() => undefined);
      if (!res.ok) throw toApiError(res.status, body);
      return body as FileInfo;
    },
  });
}

export function useCreateJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      file_id: string;
      sheet: string | null;
      header_row: number;
      column_mapping: ColumnMappingEntry[];
      options: { use_llm: boolean; use_feedback: boolean };
    }) => unwrap(api.POST("/jobs", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useJobs(page: number) {
  return useQuery({
    queryKey: keys.jobs(page),
    queryFn: () => unwrap(api.GET("/jobs", { params: { query: { page, page_size: 20 } } })),
    placeholderData: keepPreviousData,
    // Если в списке есть идущие задачи — обновляем прогресс.
    refetchInterval: (q) => (q.state.data?.items.some((j) => isActive(j.status)) ? 2000 : false),
  });
}

export function useJob(jobId: string) {
  return useQuery({
    queryKey: keys.job(jobId),
    queryFn: () => unwrap(api.GET("/jobs/{job_id}", { params: { path: { job_id: jobId } } })),
    refetchInterval: (q) => (isActive(q.state.data?.status) ? 1500 : false),
  });
}

export function useCancelJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => unwrap(api.POST("/jobs/{job_id}/cancel", { params: { path: { job_id: jobId } } })),
    onSuccess: (job) => {
      qc.setQueryData(keys.job(job.job_id), job);
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useDeleteJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (jobId: string) => {
      const { error, response } = await api.DELETE("/jobs/{job_id}", { params: { path: { job_id: jobId } } });
      if (!response.ok) throw toApiError(response.status, error);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

// ---------------------------------------------------------------- позиции

export type ItemsParams = {
  page: number;
  page_size: number;
  band?: "green" | "yellow" | "red";
  status?: Item["status"];
  q?: string;
  flag?: NonNullable<Item["flags"]>[number];
  group_id?: string;
  sort?: "row_index" | "confidence_asc";
};

/**
 * Позиции задачи. Отдельно их не опрашиваем: страница задачи сама сбрасывает этот кэш,
 * когда в опросе задачи меняется число обработанных позиций, — таблица и прогресс не расходятся.
 */
export function useItems(jobId: string, params: ItemsParams) {
  return useQuery({
    queryKey: keys.items(jobId, params),
    queryFn: () => unwrap(api.GET("/jobs/{job_id}/items", { params: { path: { job_id: jobId }, query: params } })),
    placeholderData: keepPreviousData,
  });
}

/** Карточка позиции. Пока она грузится, показываем строку из таблицы — карточка открывается мгновенно. */
export function useItem(itemId: string | null, fromList?: Item) {
  return useQuery({
    queryKey: keys.item(itemId ?? ""),
    queryFn: () => unwrap(api.GET("/items/{item_id}", { params: { path: { item_id: itemId! } } })),
    enabled: !!itemId,
    placeholderData: fromList && fromList.item_id === itemId ? fromList : undefined,
  });
}

/** После решения по позиции обновляем её карточку, таблицу и счётчики задачи. */
function useAfterDecision() {
  const qc = useQueryClient();
  return (item: Item) => {
    qc.setQueryData(keys.item(item.item_id), item);
    qc.invalidateQueries({ queryKey: ["items", item.job_id] });
    qc.invalidateQueries({ queryKey: keys.job(item.job_id) });
    qc.invalidateQueries({ queryKey: keys.stats });
  };
}

export function useConfirmItem() {
  const after = useAfterDecision();
  return useMutation({
    mutationFn: (itemId: string) =>
      unwrap(api.POST("/items/{item_id}/confirm", { params: { path: { item_id: itemId } } })),
    onSuccess: after,
  });
}

export function useCorrectItem() {
  const after = useAfterDecision();
  return useMutation({
    mutationFn: (v: { itemId: string; ktru_code: string; reason?: string | null }) =>
      unwrap(
        api.PUT("/items/{item_id}/decision", {
          params: { path: { item_id: v.itemId } },
          body: { ktru_code: v.ktru_code, reason: v.reason || null },
        }),
      ),
    onSuccess: after,
  });
}

export function useConfirmBulk(jobId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { min_confidence: number; skip_flagged: boolean }) =>
      unwrap(api.POST("/jobs/{job_id}/confirm-bulk", { params: { path: { job_id: jobId } }, body: v })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["items", jobId] });
      qc.invalidateQueries({ queryKey: ["item"] });
      qc.invalidateQueries({ queryKey: keys.job(jobId) });
    },
  });
}

// ---------------------------------------------------------------- выгрузка

export type ExportFormat = "source" | "xlsx" | "csv" | "json";

export function useExport() {
  return useMutation({
    mutationFn: async (v: { job: Job; format: ExportFormat }) => {
      const res = await rawFetch(`/jobs/${v.job.job_id}/export?format=${v.format}&allow_unreviewed=true`);
      if (!res.ok) throw toApiError(res.status, await res.json().catch(() => undefined));
      const blob = await res.blob();
      const unreviewed = Number(res.headers.get("X-Unreviewed-Count") ?? 0);
      const filename = parseFilename(res.headers.get("Content-Disposition")) ?? `result_${v.job.filename}`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      return { unreviewed, filename };
    },
  });
}

/** Имя файла из Content-Disposition; кириллица приходит как filename*=UTF-8''... (см. openapi.yaml). */
function parseFilename(header: string | null): string | null {
  if (!header) return null;
  const star = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (star) return decodeURIComponent(star[1]);
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1] : null;
}

// ---------------------------------------------------------------- каталог, быстрая проверка, качество

export function useCatalogSearch(q: string, versionId?: string) {
  return useQuery({
    queryKey: keys.catalogSearch(q, versionId),
    queryFn: () => unwrap(api.GET("/catalog/search", { params: { query: { q, version_id: versionId, limit: 10 } } })),
    enabled: q.trim().length >= 2,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
}

export function useCatalogItem(code: string | null, versionId?: string) {
  return useQuery({
    queryKey: ["catalog-item", code, versionId],
    queryFn: () =>
      unwrap(
        api.GET("/catalog/items/{ktru_code}", {
          params: { path: { ktru_code: code! }, query: { version_id: versionId } },
        }),
      ),
    enabled: !!code,
    staleTime: 5 * 60_000,
  });
}

export function useCatalogVersions() {
  return useQuery({
    queryKey: ["catalog-versions"],
    queryFn: () => unwrap(api.GET("/catalog/versions")),
    staleTime: 60_000,
    retry: false,
  });
}

export function useClassify() {
  return useMutation({
    mutationFn: (texts: string[]) =>
      unwrap(api.POST("/classify", { body: { items: texts.map((text) => ({ text })) } })),
  });
}

export type EvalSplit = "dev" | "test";

export function useQuality() {
  return useQuery({
    queryKey: keys.quality,
    queryFn: () => unwrap(api.GET("/quality/latest")),
    // Пока идёт прогон — опрашиваем раз в 3 с: оценка 700 позиций занимает 1–2 минуты.
    refetchInterval: (q) =>
      q.state.data?.dev?.status === "running" || q.state.data?.test?.status === "running" ? 3000 : false,
  });
}

/** Запуск оценки на размеченном наборе. Если прогон этого набора уже идёт, бэкенд вернёт его, второй не начнётся. */
export function useRunQuality() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (split: EvalSplit) => unwrap(api.POST("/quality/run", { body: { split } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.quality }),
  });
}

export function useStats() {
  return useQuery({ queryKey: keys.stats, queryFn: () => unwrap(api.GET("/stats")) });
}
