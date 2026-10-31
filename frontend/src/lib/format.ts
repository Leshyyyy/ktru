import type { Band, ColumnRole, ItemFlag, ItemStatus, JobStatus } from "../api/types";

/**
 * Проценты округляем вниз, а не к ближайшему: уверенность 0.846 — это «84%». Иначе жёлтая позиция показывалась
 * как «85%» и выглядела прошедшей зелёный порог, хотя «Подтвердить все от 85%» её справедливо пропускает.
 * Добавка 1e-9 гасит ошибку двоичной дроби: 0.57 * 100 = 56.99999999999999, а порог 57% должен остаться 57%.
 */
export const pctInt = (v: number) => Math.floor(v * 100 + 1e-9);

export const pct = (v: number | null | undefined) => (v == null ? "—" : `${pctInt(v)}%`);

/** Дробь по-русски, с запятой: 0.06 → «0,060» при digits = 3. */
export const decimal = (v: number, digits: number) => v.toFixed(digits).replace(".", ",");

export const bandOf = (c: number | null | undefined, t: { green: number; yellow: number }): Band | null =>
  c == null ? null : c >= t.green ? "green" : c >= t.yellow ? "yellow" : "red";

/** Цвета зон уверенности. Цвет никогда не единственный носитель смысла: рядом всегда число или подпись. */
export const BAND: Record<Band, { color: string; label: string; hint: string }> = {
  green: { color: "teal", label: "Уверенно", hint: "Можно подтверждать без проверки" },
  yellow: { color: "yellow", label: "Проверить", hint: "Посмотрите альтернативы" },
  red: { color: "red", label: "Сомнительно", hint: "Скорее всего нужен ручной выбор кода" },
};

export const JOB_STATUS: Record<JobStatus, { label: string; color: string }> = {
  queued: { label: "В очереди", color: "gray" },
  parsing: { label: "Разбор файла", color: "blue" },
  processing: { label: "Обработка", color: "blue" },
  done: { label: "Готово", color: "teal" },
  done_with_errors: { label: "Готово, есть ошибки", color: "orange" },
  failed: { label: "Ошибка", color: "red" },
  cancelled: { label: "Отменена", color: "gray" },
};

export const ITEM_STATUS: Record<ItemStatus, { label: string; color: string }> = {
  pending: { label: "В очереди", color: "gray" },
  processed: { label: "Обработана", color: "blue" },
  needs_review: { label: "Требует проверки", color: "orange" },
  confirmed: { label: "Подтверждена", color: "teal" },
  corrected: { label: "Исправлена", color: "violet" },
  failed: { label: "Ошибка", color: "red" },
};

export const FLAG: Record<ItemFlag, { label: string; short: string; hint: string }> = {
  required_missing: {
    label: "Нет обязательной характеристики",
    short: "нет обяз. характеристики",
    hint: "У предложенного кода есть обязательная характеристика КТРУ, которой нет в описании позиции.",
  },
  archived_code: {
    label: "Код в архиве",
    short: "код в архиве каталога",
    hint: "Код помечен в каталоге как архивный. Подберите действующий.",
  },
  low_extraction_confidence: {
    label: "Атрибуты распознаны неуверенно",
    short: "атрибуты неуверенно",
    hint: "Наименование, бренд или модель извлечены с низкой уверенностью.",
  },
};

export const ROLE: Record<ColumnRole, string> = {
  name: "Наименование",
  description: "Описание",
  characteristics: "Характеристики",
  quantity: "Количество",
  unit: "Ед. измерения",
  ignore: "Не использовать",
};

const dtf = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
export const dateTime = (iso: string | null | undefined) => (iso ? dtf.format(new Date(iso)) : "—");

export function duration(seconds: number | null | undefined) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} с`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return s ? `${m} мин ${s} с` : `${m} мин`;
}

export function plural(n: number, one: string, few: string, many: string) {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

export const fileSize = (b: number) =>
  b < 1024 ? `${b} Б` : b < 1024 ** 2 ? `${(b / 1024).toFixed(0)} КБ` : `${(b / 1024 ** 2).toFixed(1)} МБ`;
