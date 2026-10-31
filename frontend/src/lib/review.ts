import type { Job } from "../api/types";

/** Статусы позиций, которые человек уже проверил. API фильтрует по одному статусу, поэтому выбираем один из двух. */
export type ReviewedStatus = "confirmed" | "corrected";

export const isReviewed = (status: string | undefined): status is ReviewedStatus =>
  status === "confirmed" || status === "corrected";

/**
 * Что открыть по плитке «Проверено»: подтверждённые, если они есть, иначе исправленные.
 * Раньше всегда открывались подтверждённые, и когда человек только исправлял коды, плитка показывала «2»,
 * а по нажатию — пустой список.
 */
export const reviewedStatus = (s: Pick<Job["summary"], "confirmed" | "corrected">): ReviewedStatus =>
  (s.confirmed ?? 0) === 0 && (s.corrected ?? 0) > 0 ? "corrected" : "confirmed";
