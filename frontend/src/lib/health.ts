import type { Health } from "../api/types";

/**
 * Причина отката на заглушку: /health отвечает «готово», а у моделей есть пояснение. Так бэкенд сообщает,
 * что ML-воркер не получил каталог и отвечает демонстрационными кодами 99.99.* (core/ml.catalog_fallback),
 * или что PIPELINE=stub. Без пояснения у моделей всё в порядке.
 */
export function stubReason(health: Health | undefined): string | null {
  const models = health?.components.models;
  return health?.ready && models?.ok && models.detail ? models.detail : null;
}

/** Что мешает системе стать готовой: пояснения компонентов с ok = false, тексты бэкенда как есть. */
export function notReadyReasons(health: Health | undefined): string[] {
  if (!health || health.ready) return [];
  return Object.values(health.components).flatMap((c) => (c && !c.ok && c.detail ? [c.detail] : []));
}
