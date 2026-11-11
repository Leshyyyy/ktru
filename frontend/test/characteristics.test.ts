// Счётчик «не заполнено N из M» для блока обязательных характеристик. Запуск: npm test.
import assert from "node:assert/strict";
import { test } from "node:test";
import { requiredCounters } from "../src/lib/characteristics.ts";
import type { ItemCharacteristic } from "../src/api/types.ts";

const ch = (required: boolean, status: ItemCharacteristic["status"]): ItemCharacteristic => ({
  name: "Х",
  required,
  status,
});

test("считает только обязательные и только статус missing", () => {
  assert.deepEqual(
    requiredCounters([ch(true, "match"), ch(true, "missing"), ch(true, "filled"), ch(false, "missing")]),
    { total: 3, missing: 1 },
  );
});

test("незаполненными считаются обязательные missing, а не mismatch", () => {
  assert.deepEqual(requiredCounters([ch(true, "mismatch"), ch(true, "missing")]), { total: 2, missing: 1 });
});

test("пустой список и null", () => {
  assert.deepEqual(requiredCounters([]), { total: 0, missing: 0 });
  assert.deepEqual(requiredCounters(null), { total: 0, missing: 0 });
  assert.deepEqual(requiredCounters(undefined), { total: 0, missing: 0 });
});

test("у кода без обязательных характеристик счётчик нулевой", () => {
  assert.deepEqual(requiredCounters([ch(false, "match"), ch(false, "missing")]), { total: 0, missing: 0 });
});
