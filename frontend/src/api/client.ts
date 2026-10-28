import createClient from "openapi-fetch";
import type { paths } from "./schema";
import type { ApiError, ErrorCode } from "./types";

/**
 * Режим работы задаётся при сборке (VITE_API_MODE):
 *   memory — запросы обрабатывает имитация бэкенда прямо в браузере (src/mock);
 *   http   — обычные запросы на /api/v1 (Prism или настоящий бэкенд).
 * Остальной код приложения не знает, какой режим включён: подменяется только функция fetch.
 */
export const API_MODE: "memory" | "http" = import.meta.env.VITE_API_MODE === "http" ? "http" : "memory";

export const BASE_URL = "/api/v1";

let mockFetch: ((req: Request) => Promise<Response>) | null = null;

async function transport(input: Request): Promise<Response> {
  if (API_MODE === "http") return fetch(input);
  // Имитацию грузим лениво: в сборку для настоящего бэкенда она не попадает.
  if (!mockFetch) mockFetch = (await import("../mock")).mockFetch;
  return mockFetch(input);
}

export const api = createClient<paths>({ baseUrl: BASE_URL, fetch: transport });

/** Запрос в обход типизированного клиента: загрузка файла (multipart) и выгрузка (бинарный ответ). */
export function rawFetch(path: string, init?: RequestInit): Promise<Response> {
  return transport(new Request(new URL(BASE_URL + path, window.location.origin), init));
}

/** Ошибка API в нашем формате {"error": {code, message, details}}. message — по-русски, для человека. */
export class ApiRequestError extends Error {
  constructor(
    public status: number,
    public code: ErrorCode | "network_error",
    message: string,
    public details: Record<string, unknown> | null = null,
  ) {
    super(message);
  }
}

export function toApiError(status: number, body: unknown): ApiRequestError {
  const e = (body as ApiError | undefined)?.error;
  if (e?.code && e.message) return new ApiRequestError(status, e.code, e.message, e.details ?? null);
  return new ApiRequestError(status, "internal_error", `Сервер ответил ошибкой ${status}.`);
}

/** Достаёт data из ответа openapi-fetch или бросает ApiRequestError — так с ним удобно работать в react-query. */
export async function unwrap<T>(p: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  let res;
  try {
    res = await p;
  } catch {
    throw new ApiRequestError(0, "network_error", "Нет связи с сервером. Проверьте, что система запущена.");
  }
  if (res.error !== undefined || !res.response.ok) throw toApiError(res.response.status, res.error);
  return res.data as T;
}
