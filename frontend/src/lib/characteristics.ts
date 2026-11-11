import type { ItemCharacteristic } from "../api/types";

/**
 * Счётчик «не заполнено N из M» для блока обязательных характеристик.
 * N — обязательные со статусом `missing` (не найдены в тексте и не введены вручную), M — все обязательные.
 */
export function requiredCounters(chars: ItemCharacteristic[] | null | undefined): { total: number; missing: number } {
  const required = (chars ?? []).filter((c) => c.required);
  return {
    total: required.length,
    missing: required.filter((c) => c.status === "missing").length,
  };
}
