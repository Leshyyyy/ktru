/**
 * Имитация бэкенда прямо в браузере (режим VITE_API_MODE=memory).
 *
 * Отвечает на те же запросы и в том же формате, что описан в contracts/openapi.yaml, поэтому фронт
 * работает с ней так же, как будет работать с настоящим бэкендом. Данные живут в памяти вкладки
 * и пропадают при перезагрузке. Обработка задачи имитируется по времени: позиции «дозревают»
 * со скоростью RATE штук в секунду, так что прогресс, частичные результаты и отмену можно посмотреть вживую.
 */
import type {
  ColumnMappingEntry,
  FileInfo,
  FinalAnswer,
  Item,
  ItemStatus,
  Job,
  JobStatus,
  LayoutHint,
} from "../api/types";
import { byCode, CATALOG, CATALOG_VERSION_ID, toPublic } from "./catalog";
import { detectHeaderRow, parseFile, suggestRoles, type ParsedSheet } from "./parse";
import { classify, hash, type MockResult } from "./pipeline";
import { SAMPLE_FILENAME, SAMPLE_HEADERS, SAMPLE_ROWS } from "./sample";

const RATE = 12; // позиций в секунду
const QUEUED_MS = 600;
const PARSING_MS = 900;
const THRESHOLDS = { green: 0.85, yellow: 0.5 };
const LIMITS = { max_file_mb: 50, max_rows: 5000, max_items_per_job: 1000, classify_max_items: 20 };

// ---------------------------------------------------------------- хранилище

type Decision = {
  kind: "confirm" | "correct";
  ktru_code: string;
  via: "manual" | "bulk";
  reason: string | null;
  at: number;
};
type ItemRec = {
  item_id: string;
  job_id: string;
  row_index: number;
  raw_text: string;
  raw_row: string[];
  columns: Record<string, string>;
  result: MockResult;
  readyAt: number;
  decisions: Decision[];
};
type FileRec = { file_id: string; filename: string; size: number; sha: string; sheets: ParsedSheet[]; info: FileInfo };
type JobRec = {
  job_id: string;
  file_id: string;
  filename: string;
  headers: string[];
  createdAt: number;
  cancelledAt: number | null;
  items: ItemRec[];
  effective_mode: "normal" | "degraded";
};

const files = new Map<string, FileRec>();
const jobs = new Map<string, JobRec>();
const items = new Map<string, ItemRec>();
const layouts = new Map<string, LayoutHint>();
const shaToJob = new Map<string, string>();

const uuid = (): string =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) =>
        (Number(c) ^ ((Math.random() * 16) >> (Number(c) / 4))).toString(16),
      );

/** uuid5-подобный стабильный id группы из job_id и norm_key (как в schema.sql). */
const groupId = (jobId: string, key: string) => {
  const h = (hash(jobId + "|" + key).toString(16) + hash(key + "|" + jobId).toString(16)).padEnd(32, "0");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-5${h.slice(13, 16)}-8${h.slice(17, 20)}-${h.slice(20, 32)}`;
};

const iso = (ms: number) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");

// ---------------------------------------------------------------- представления по контракту

function cutoff(job: JobRec) {
  return Math.min(Date.now(), job.cancelledAt ?? Infinity);
}

function itemStatus(it: ItemRec, job: JobRec): ItemStatus {
  if (it.readyAt > cutoff(job)) return "pending";
  const last = it.decisions.at(-1);
  if (last) return last.kind === "confirm" ? "confirmed" : "corrected";
  if (it.result.error) return "failed";
  return (it.result.predictions[0]?.confidence ?? 0) >= THRESHOLDS.green ? "processed" : "needs_review";
}

const bandOf = (c: number) => (c >= THRESHOLDS.green ? "green" : c >= THRESHOLDS.yellow ? "yellow" : "red");

function itemView(it: ItemRec): Item {
  const job = jobs.get(it.job_id)!;
  const status = itemStatus(it, job);
  const ready = status !== "pending";
  const preds = ready ? it.result.predictions : [];
  const top = preds[0];
  const key = ready ? it.result.norm_key : null;
  const size = key ? job.items.filter((x) => x.result.norm_key === key && x.readyAt <= cutoff(job)).length : 1;
  const last = it.decisions.at(-1);
  let final: FinalAnswer | null = null;
  if (last)
    final = {
      ktru_code: last.ktru_code,
      name: byCode.get(last.ktru_code)?.name ?? null,
      source: "user",
      confidence: null,
    };
  else if (top) final = { ktru_code: top.ktru_code, name: top.name, source: "prediction", confidence: top.confidence };
  return {
    item_id: it.item_id,
    job_id: it.job_id,
    row_index: it.row_index,
    raw_text: it.raw_text,
    status,
    band: top ? bandOf(top.confidence) : null,
    path: ready ? it.result.path : null,
    flags: ready ? it.result.flags : [],
    group_id: key && size > 1 ? groupId(it.job_id, key) : null,
    group_size: size,
    extracted: ready ? it.result.extracted : null,
    predictions: preds,
    final,
    error: ready ? it.result.error : null,
  };
}

function jobView(job: JobRec): Job {
  const now = Date.now();
  const startedAt = job.createdAt + QUEUED_MS;
  const parsedAt = startedAt + PARSING_MS;
  const views = job.items.map(itemView);
  const ready = views.filter((v) => v.status !== "pending");
  const failed = ready.filter((v) => v.status === "failed").length;
  const processed = ready.length - failed;
  const lastReady = Math.max(parsedAt, ...job.items.map((i) => i.readyAt));

  let status: JobStatus;
  if (job.cancelledAt) status = "cancelled";
  else if (now < startedAt) status = "queued";
  else if (now < parsedAt) status = "parsing";
  else if (now < lastReady) status = "processing";
  else status = failed ? "done_with_errors" : "done";

  const parsed = status !== "queued" && status !== "parsing";
  const total = parsed ? job.items.length : 0;
  const summary = { green: 0, yellow: 0, red: 0, confirmed: 0, corrected: 0 };
  for (const v of ready) {
    if (v.band) summary[v.band]++;
    if (v.status === "confirmed") summary.confirmed++;
    if (v.status === "corrected") summary.corrected++;
  }
  const finished = status === "done" || status === "done_with_errors" || status === "cancelled";
  return {
    job_id: job.job_id,
    status,
    file_id: job.file_id,
    filename: job.filename,
    total_items: total,
    processed_items: processed,
    failed_items: failed,
    progress: total ? Math.round(((processed + failed) / total) * 1000) / 1000 : 0,
    eta_seconds: status === "processing" ? Math.ceil((total - ready.length) / RATE) : null,
    effective_mode: job.effective_mode,
    catalog_version_id: CATALOG_VERSION_ID,
    summary,
    error: null,
    error_code: null,
    created_at: iso(job.createdAt),
    started_at: now >= startedAt ? iso(startedAt) : null,
    finished_at: finished ? iso(job.cancelledAt ?? lastReady) : null,
  };
}

// ---------------------------------------------------------------- создание задач

const ROLE_KEYS = ["name", "description", "characteristics", "quantity", "unit"] as const;

function createJob(opts: {
  file: { file_id: string; filename: string };
  headers: string[];
  rows: string[][];
  mapping: ColumnMappingEntry[];
  createdAt: number;
  degraded?: boolean;
}): JobRec {
  const job: JobRec = {
    job_id: uuid(),
    file_id: opts.file.file_id,
    filename: opts.file.filename,
    headers: opts.headers,
    createdAt: opts.createdAt,
    cancelledAt: null,
    items: [],
    effective_mode: opts.degraded ? "degraded" : "normal",
  };
  const parsedAt = opts.createdAt + QUEUED_MS + PARSING_MS;
  const nonEmpty = opts.rows.filter((r) => r.some((c) => c.trim()));
  nonEmpty.forEach((row, i) => {
    const columns: Record<string, string> = {};
    for (const role of ROLE_KEYS) {
      const parts = opts.mapping
        .filter((m) => m.role === role)
        .map((m) => row[m.index]?.trim() ?? "")
        .filter(Boolean);
      if (parts.length) columns[role] = parts.join(", ");
    }
    // Склейка name + description + characteristics — то, что уходит в ML (RawItem.text).
    const raw_text = [columns.name, columns.description, columns.characteristics].filter(Boolean).join(", ");
    const it: ItemRec = {
      item_id: uuid(),
      job_id: job.job_id,
      row_index: i,
      raw_text,
      raw_row: row,
      columns,
      result: classify(raw_text, columns),
      readyAt: parsedAt + (i + 1) * (1000 / RATE),
      decisions: [],
    };
    job.items.push(it);
    items.set(it.item_id, it);
  });
  jobs.set(job.job_id, job);
  return job;
}

// Одна уже обработанная задача, чтобы список задач и таблица не были пустыми при первом открытии.
(function seed() {
  const file_id = uuid();
  const mapping: ColumnMappingEntry[] = [
    { index: 1, role: "name" },
    { index: 2, role: "characteristics" },
    { index: 3, role: "quantity" },
    { index: 4, role: "unit" },
  ];
  const job = createJob({
    file: { file_id, filename: SAMPLE_FILENAME },
    headers: SAMPLE_HEADERS,
    rows: SAMPLE_ROWS,
    mapping,
    createdAt: Date.now() - 26 * 3600_000,
  });
  // Пара решений «как будто пользователь уже поработал».
  const at = Date.now() - 25 * 3600_000;
  job.items.slice(0, 4).forEach((it) => {
    const top = it.result.predictions[0];
    if (top && top.confidence >= THRESHOLDS.green)
      it.decisions.push({ kind: "confirm", ktru_code: top.ktru_code, via: "manual", reason: null, at });
  });
  layouts.set(headerHash(SAMPLE_HEADERS), { header_row: 0, column_mapping: mapping });
})();

function headerHash(headers: string[]) {
  return String(hash(headers.map((h) => h.trim().toLowerCase()).join("|")));
}

// ---------------------------------------------------------------- маршрутизация запросов

type Ctx = { req: Request; url: URL; params: Record<string, string> };
type Handler = (c: Ctx) => Promise<Response> | Response;

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });

const err = (status: number, code: string, message: string, details: Record<string, unknown> | null = null) =>
  json({ error: { code, message, details } }, status);

const notFound = (object: string, message = "Не найдено.") => err(404, "not_found", message, { object });

// ---------------------------------------------------------------- оценка качества (демо-цифры)

const EVAL_MS = 4000; // «прогон» в имитации длится 4 с, чтобы было видно состояние «идёт оценка»
const EVAL_DEMO: Record<"dev" | "test", { samples: number; metrics: Record<string, number> }> = {
  dev: {
    samples: 210,
    metrics: {
      top1_full_code: 0.88,
      top1_any_valid: 0.9,
      top1_okpd2: 0.94,
      top3: 0.96,
      attributes_accuracy: 0.92,
      ece: 0.041,
      fast_path_share: 0.81,
    },
  },
  test: {
    samples: 90,
    metrics: {
      top1_full_code: 0.86,
      top1_any_valid: 0.88,
      top1_okpd2: 0.93,
      top3: 0.95,
      attributes_accuracy: 0.91,
      ece: 0.048,
      fast_path_share: 0.79,
    },
  },
};
type EvalRec = { run_id: string; split: "dev" | "test"; started: number };
const evalRuns: Partial<Record<"dev" | "test", EvalRec>> = {
  dev: { run_id: uuid(), split: "dev", started: Date.now() - 5 * 3600_000 },
  test: { run_id: uuid(), split: "test", started: Date.now() - 5 * 3600_000 },
};
const evalView = (r: EvalRec) => {
  const done = Date.now() - r.started >= EVAL_MS;
  const demo = EVAL_DEMO[r.split];
  return {
    run_id: r.run_id,
    split: r.split,
    status: done ? "done" : "running",
    created_at: iso(r.started),
    config: { extractor: "hybrid", reranker: "cross_encoder", device: "cpu", demo: true },
    metrics: done
      ? {
          samples: demo.samples,
          seconds_per_1000: 187.4,
          ...demo.metrics,
          attributes_by_field: { name: 0.95, brand: 0.93, model: 0.88 },
        }
      : null,
  };
};

const routes: [string, string, Handler][] = [
  [
    "GET",
    "/health",
    () =>
      json({
        ready: true,
        components: {
          database: { ok: true, detail: null },
          redis: { ok: true, detail: null },
          models: { ok: true, detail: "демо-режим" },
          catalog: { ok: true, detail: `демо-каталог, ${CATALOG.length} позиций` },
        },
      }),
  ],
  [
    "GET",
    "/config",
    () =>
      json({
        limits: LIMITS,
        thresholds: THRESHOLDS,
        mode: { extractor: "rules", reranker: "none", device: "cpu", llm_enabled: false },
      }),
  ],

  [
    "POST",
    "/files",
    async ({ req }) => {
      const form = await req.formData();
      const file = form.get("file");
      if (!(file instanceof File))
        return err(400, "invalid_request", "Некорректный запрос: нет поля file.", { field: "file" });
      const ext = file.name.toLowerCase().split(".").pop() ?? "";
      if (!["xlsx", "csv", "pdf"].includes(ext))
        return err(415, "unsupported_file_type", "Поддерживаются файлы xlsx, csv и pdf.");
      if (file.size > LIMITS.max_file_mb * 1024 * 1024)
        return err(413, "file_too_large", "Файл больше 50 МБ. Разбейте его на части.");
      let sheets: ParsedSheet[];
      try {
        sheets = await parseFile(file);
      } catch {
        return err(
          422,
          "file_unreadable",
          "Не удалось прочитать файл. Проверьте, что он не повреждён и не защищён паролем.",
        );
      }
      if (!sheets.some((s) => s.rows.length))
        return err(422, "file_unreadable", "В файле не нашлось ни одной таблицы.");
      const sha = await sha256(file);
      const file_id = uuid();
      let hint: LayoutHint | null = null;
      const info: FileInfo = {
        file_id,
        filename: file.name,
        size_bytes: file.size,
        duplicate_of_job_id: shaToJob.get(sha) ?? null,
        layout_hint: null,
        sheets: sheets.map((s) => {
          const header_row = detectHeaderRow(s.rows);
          const headers = (s.rows[header_row] ?? []).map((h) => h.trim());
          const data = s.rows.slice(header_row + 1).filter((r) => r.some((c) => c.trim()));
          const roles = suggestRoles(headers, data.slice(0, 10));
          hint ??= layouts.get(headerHash(headers)) ?? null;
          return {
            name: s.name,
            row_count: s.rowCountKnown ? data.length : null,
            header_row,
            columns: headers.map((header, index) => ({
              index,
              header: header || `Колонка ${index + 1}`,
              sample: data
                .slice(0, 3)
                .map((r) => r[index] ?? "")
                .filter(Boolean),
              suggested_role: roles[index],
            })),
            preview: data.slice(0, 10).map((r) => headers.map((_, i) => r[i] ?? "")),
          };
        }),
      };
      info.layout_hint = hint;
      files.set(file_id, { file_id, filename: file.name, size: file.size, sha, sheets, info });
      return json(info, 201);
    },
  ],

  [
    "POST",
    "/jobs",
    async ({ req }) => {
      const body = await req.json().catch(() => null);
      if (!body?.file_id || !Array.isArray(body.column_mapping))
        return err(400, "invalid_request", "Некорректный запрос: нужны поля file_id и column_mapping.");
      const f = files.get(body.file_id);
      if (!f) return notFound("file", "Файл не найден. Загрузите его ещё раз.");
      const mapping: ColumnMappingEntry[] = body.column_mapping;
      const count = (r: string) => mapping.filter((m) => m.role === r).length;
      if (count("name") !== 1)
        return err(422, "mapping_invalid", "Выберите ровно одну колонку с наименованием товара.");
      if (count("quantity") > 1 || count("unit") > 1)
        return err(422, "mapping_invalid", "Колонка количества и колонка единиц измерения — не больше одной каждая.");
      const sheet = f.sheets.find((s) => s.name === body.sheet) ?? f.sheets[0];
      const header_row = body.header_row ?? detectHeaderRow(sheet.rows);
      const headers = sheet.rows[header_row] ?? [];
      const rows = sheet.rows.slice(header_row + 1);
      const nonEmpty = rows.filter((r) => r.some((c) => c.trim())).length;
      if (nonEmpty > LIMITS.max_items_per_job)
        return err(
          422,
          "limit_exceeded",
          `В файле больше ${LIMITS.max_items_per_job} позиций. Разбейте его на части.`,
          { max_items_per_job: LIMITS.max_items_per_job, found: nonEmpty },
        );
      const job = createJob({
        // degraded — LLM просили, но она недоступна (в демо LLM выключена всегда).
        file: f,
        headers,
        rows,
        mapping,
        createdAt: Date.now(),
        degraded: body.options?.use_llm === true,
      });
      shaToJob.set(f.sha, job.job_id);
      layouts.set(headerHash(headers), { header_row, column_mapping: mapping });
      return json(jobView(job), 202);
    },
  ],

  [
    "GET",
    "/jobs",
    ({ url }) => {
      const page = Number(url.searchParams.get("page") ?? 1);
      const size = Number(url.searchParams.get("page_size") ?? 50);
      const all = [...jobs.values()].sort((a, b) => b.createdAt - a.createdAt).map(jobView);
      return json({ items: all.slice((page - 1) * size, page * size), total: all.length, page, page_size: size });
    },
  ],

  [
    "GET",
    "/jobs/:job_id",
    ({ params }) => {
      const job = jobs.get(params.job_id);
      return job ? json(jobView(job)) : notFound("job", "Задача не найдена.");
    },
  ],

  [
    "DELETE",
    "/jobs/:job_id",
    ({ params }) => {
      const job = jobs.get(params.job_id);
      if (!job) return notFound("job", "Задача не найдена.");
      job.items.forEach((i) => items.delete(i.item_id));
      jobs.delete(job.job_id);
      for (const [sha, id] of shaToJob) if (id === job.job_id) shaToJob.delete(sha);
      return json(null, 204);
    },
  ],

  [
    "POST",
    "/jobs/:job_id/cancel",
    ({ params }) => {
      const job = jobs.get(params.job_id);
      if (!job) return notFound("job", "Задача не найдена.");
      const v = jobView(job);
      if (!["queued", "parsing", "processing"].includes(v.status))
        return err(409, "invalid_state", "Задача уже завершена, отменить её нельзя.", { status: v.status });
      job.cancelledAt = Date.now();
      return json(jobView(job));
    },
  ],

  [
    "GET",
    "/jobs/:job_id/items",
    ({ params, url }) => {
      const job = jobs.get(params.job_id);
      if (!job) return notFound("job", "Задача не найдена.");
      const sp = url.searchParams;
      const page = Number(sp.get("page") ?? 1);
      const size = Math.min(200, Number(sp.get("page_size") ?? 50));
      const q = sp.get("q")?.toLowerCase().trim();
      let list = job.items.map(itemView);
      if (sp.get("band")) list = list.filter((v) => v.band === sp.get("band"));
      if (sp.get("status")) list = list.filter((v) => v.status === sp.get("status"));
      if (sp.get("flag")) list = list.filter((v) => v.flags?.includes(sp.get("flag") as never));
      if (sp.get("group_id")) list = list.filter((v) => v.group_id === sp.get("group_id"));
      if (q) list = list.filter((v) => v.raw_text.toLowerCase().includes(q));
      if (sp.get("sort") === "confidence_asc")
        list.sort((a, b) => (a.predictions[0]?.confidence ?? -1) - (b.predictions[0]?.confidence ?? -1));
      return json({ items: list.slice((page - 1) * size, page * size), total: list.length, page, page_size: size });
    },
  ],

  [
    "POST",
    "/jobs/:job_id/confirm-bulk",
    async ({ params, req }) => {
      const job = jobs.get(params.job_id);
      if (!job) return notFound("job", "Задача не найдена.");
      const body = await req.json().catch(() => null);
      const min = Number(body?.min_confidence);
      if (!(min >= 0 && min <= 1))
        return err(400, "invalid_request", "min_confidence должен быть от 0 до 1.", { field: "min_confidence" });
      const skipFlagged = body?.skip_flagged ?? true;
      let n = 0;
      for (const it of job.items) {
        const v = itemView(it);
        const top = v.predictions[0];
        if (!top || !["processed", "needs_review"].includes(v.status) || top.confidence < min) continue;
        if (skipFlagged && v.flags?.length) continue;
        it.decisions.push({ kind: "confirm", ktru_code: top.ktru_code, via: "bulk", reason: null, at: Date.now() });
        n++;
      }
      return json({ confirmed_count: n });
    },
  ],

  [
    "GET",
    "/jobs/:job_id/export",
    ({ params, url }) => {
      const job = jobs.get(params.job_id);
      if (!job) return notFound("job", "Задача не найдена.");
      const v = jobView(job);
      if (!["done", "done_with_errors", "cancelled"].includes(v.status))
        return err(409, "invalid_state", "Задача ещё обрабатывается.", { status: v.status });
      const views = job.items.map(itemView);
      const unreviewed = views.filter((x) => x.status === "processed" || x.status === "needs_review").length;
      if (url.searchParams.get("allow_unreviewed") === "false" && unreviewed)
        return err(409, "unreviewed_items", `Осталось ${unreviewed} непроверенных позиций.`, { unreviewed });
      const format = url.searchParams.get("format") ?? "source";
      const statusRu: Record<ItemStatus, string> = {
        pending: "не обработана",
        processed: "обработана",
        needs_review: "требует проверки",
        confirmed: "подтверждена",
        corrected: "исправлена",
        failed: "ошибка",
      };
      const extra = ["Код КТРУ", "Наименование КТРУ", "Уверенность, %", "Статус", "Источник", "Предупреждения"];
      const records = job.items.map((it, i) => {
        const x = views[i];
        const failed = x.status === "failed" || x.status === "pending";
        return [
          ...job.headers.map((_, c) => it.raw_row[c] ?? ""),
          failed ? "" : (x.final?.ktru_code ?? ""),
          failed ? "" : (x.final?.name ?? ""),
          x.final?.confidence != null ? String(Math.round(x.final.confidence * 100)) : "",
          statusRu[x.status],
          x.final ? (x.final.source === "user" ? "пользователь" : "модель") : "",
          (x.flags ?? []).join(", "),
        ];
      });
      const base = job.filename.replace(/\.[^.]+$/, "");
      const headers = { "X-Unreviewed-Count": String(unreviewed) };
      if (format === "json") {
        const cols = [...job.headers, ...extra];
        const data = records.map((r) => Object.fromEntries(cols.map((c, i) => [c || `Колонка ${i + 1}`, r[i]])));
        return new Response(JSON.stringify(data, null, 2), {
          headers: {
            ...headers,
            "Content-Type": "application/json",
            "Content-Disposition": disposition(`${base}_КТРУ.json`),
          },
        });
      }
      // В демо xlsx не собираем: отдаём csv, который Excel открывает так же.
      const csv = [[...job.headers, ...extra], ...records].map((r) => r.map(csvCell).join(";")).join("\r\n");
      return new Response("﻿" + csv, {
        headers: {
          ...headers,
          "Content-Type": "text/csv; charset=utf-8",
          "Content-Disposition": disposition(`${base}_КТРУ.csv`),
        },
      });
    },
  ],

  [
    "GET",
    "/items/:item_id",
    ({ params }) => {
      const it = items.get(params.item_id);
      return it ? json(itemView(it)) : notFound("item", "Позиция не найдена.");
    },
  ],

  [
    "POST",
    "/items/:item_id/confirm",
    ({ params }) => {
      const it = items.get(params.item_id);
      if (!it) return notFound("item", "Позиция не найдена.");
      const v = itemView(it);
      const top = v.predictions[0];
      if (!top) return err(409, "invalid_state", "У позиции ещё нет предложенного кода.", { status: v.status });
      if (v.status !== "confirmed")
        it.decisions.push({ kind: "confirm", ktru_code: top.ktru_code, via: "manual", reason: null, at: Date.now() });
      return json(itemView(it));
    },
  ],

  [
    "PUT",
    "/items/:item_id/decision",
    async ({ params, req }) => {
      const it = items.get(params.item_id);
      if (!it) return notFound("item", "Позиция не найдена.");
      const body = await req.json().catch(() => null);
      if (!body?.ktru_code)
        return err(400, "invalid_request", "Некорректный запрос: нужен ktru_code.", { field: "ktru_code" });
      const v = itemView(it);
      if (v.status === "pending")
        return err(409, "invalid_state", "Позиция ещё не обработана. Дождитесь результата и повторите.", {
          status: "pending",
        });
      if (!byCode.has(body.ktru_code))
        return notFound("ktru_code", `Кода ${body.ktru_code} нет в версии каталога этой задачи.`);
      const same = v.predictions[0]?.ktru_code === body.ktru_code;
      it.decisions.push({
        kind: same ? "confirm" : "correct",
        ktru_code: body.ktru_code,
        via: "manual",
        reason: body.reason ?? null,
        at: Date.now(),
      });
      return json(itemView(it));
    },
  ],

  [
    "POST",
    "/classify",
    async ({ req }) => {
      const body = await req.json().catch(() => null);
      const list = body?.items;
      if (!Array.isArray(list) || list.length < 1 || list.length > LIMITS.classify_max_items)
        return err(400, "invalid_request", "Некорректный запрос: поле items должно содержать от 1 до 20 элементов.", {
          field: "items",
        });
      await new Promise((r) => setTimeout(r, 400 + list.length * 60));
      return json({
        results: list.map(({ text }: { text: string }) => {
          const r = classify(String(text ?? ""));
          return { text, extracted: r.extracted, predictions: r.predictions, path: r.path, error: r.error };
        }),
      });
    },
  ],

  [
    "GET",
    "/catalog/search",
    ({ url }) => {
      const q = (url.searchParams.get("q") ?? "").toLowerCase().trim();
      const limit = Number(url.searchParams.get("limit") ?? 10);
      const found = CATALOG.filter((c) => c.ktru_code.startsWith(q) || c.name.toLowerCase().includes(q)).slice(
        0,
        limit,
      );
      return json({ items: found.map(toPublic) });
    },
  ],

  [
    "GET",
    "/catalog/items/:ktru_code",
    ({ params }) => {
      const c = byCode.get(decodeURIComponent(params.ktru_code));
      return c ? json(toPublic(c)) : notFound("ktru_code", "Код не найден в каталоге.");
    },
  ],

  [
    "GET",
    "/catalog/versions",
    () =>
      json({
        items: [
          {
            version_id: CATALOG_VERSION_ID,
            source: "demo_catalog.jsonl",
            status: "ready",
            items_count: CATALOG.length,
            note: "Демо-каталог: коды 99.99.* вымышленные",
            loaded_at: iso(Date.now() - 3 * 86400_000),
          },
        ],
      }),
  ],

  [
    "POST",
    "/quality/run",
    async ({ req }) => {
      const body = await req.json().catch(() => null);
      const split: unknown = body?.split;
      if (split !== "dev" && split !== "test")
        return err(400, "invalid_request", "Некорректный запрос: поле split должно быть dev или test.", {
          field: "split",
        });
      // Как бэкенд: прогон этого набора уже идёт — возвращается он, второй не запускается.
      const current = evalRuns[split];
      if (current && evalView(current).status === "running") return json(evalView(current), 202);
      evalRuns[split] = { run_id: uuid(), split, started: Date.now() };
      return json(evalView(evalRuns[split]!), 202);
    },
  ],

  ["GET", "/quality/latest", () => json({ dev: evalView(evalRuns.dev!), test: evalView(evalRuns.test!) })],

  [
    "GET",
    "/stats",
    () => {
      const all = [...items.values()].map(itemView);
      const greenManual = [...items.values()].filter((it) => {
        const top = it.result.predictions[0];
        return top && top.confidence >= THRESHOLDS.green && it.decisions.at(-1)?.via === "manual";
      });
      const corrected = greenManual.filter((it) => it.decisions.at(-1)?.kind === "correct").length;
      const failed = all.filter((v) => v.status === "failed");
      return json({
        jobs_total: jobs.size,
        items_total: all.length,
        green_correction_rate: greenManual.length ? Math.round((corrected / greenManual.length) * 1000) / 1000 : null,
        avg_seconds_per_1000: 187.4,
        failed_items_share: all.length ? Math.round((failed.length / all.length) * 1000) / 1000 : 0,
        recent_errors: failed
          .slice(0, 10)
          .map((v) => ({ job_id: v.job_id, error: v.error ?? "Ошибка", at: iso(Date.now() - 3600_000) })),
      });
    },
  ],
];

function disposition(name: string) {
  return `attachment; filename="export.${name.split(".").pop()}"; filename*=UTF-8''${encodeURIComponent(name)}`;
}

/** Значения, похожие на формулу, в csv экранируем апострофом (см. описание /export). */
function csvCell(v: string) {
  const s = /^[=+\-@]/.test(v) ? "'" + v : v;
  return /[";\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

async function sha256(file: File): Promise<string> {
  try {
    const buf = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
  } catch {
    return `${file.name}:${file.size}`;
  }
}

const compiled = routes.map(([method, pattern, handler]) => {
  const names: string[] = [];
  const re = new RegExp("^" + pattern.replace(/:(\w+)/g, (_, n) => (names.push(n), "([^/]+)")) + "$");
  return { method, re, names, handler };
});

export async function mockFetch(req: Request): Promise<Response> {
  const url = new URL(req.url);
  const path = url.pathname.replace(/^.*\/api\/v1/, "") || "/";
  // Небольшая задержка, чтобы были видны состояния загрузки, как с настоящим сервером.
  await new Promise((r) => setTimeout(r, 120 + Math.random() * 180));
  for (const r of compiled) {
    if (r.method !== req.method) continue;
    const m = r.re.exec(path);
    if (!m) continue;
    const params = Object.fromEntries(r.names.map((n, i) => [n, decodeURIComponent(m[i + 1])]));
    try {
      return await r.handler({ req, url, params });
    } catch (e) {
      console.error("[mock]", e);
      return err(500, "internal_error", "Внутренняя ошибка имитации сервера.");
    }
  }
  return err(404, "not_found", `Нет такого метода: ${req.method} ${path}`, { object: "route" });
}
