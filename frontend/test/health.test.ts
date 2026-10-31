// Тесты разбора /health для плашек «система не готова» и «работает заглушка». Запуск: npm test.
import assert from "node:assert/strict";
import { test } from "node:test";
import { notReadyReasons, stubReason } from "../src/lib/health.ts";

const FALLBACK = "Каталог КТРУ не скачался — работает демонстрационная заглушка с кодами 99.99.*.";
const ok = { ok: true, detail: null };

test("готова и без пояснения у моделей — не заглушка", () => {
  const h = {
    ready: true,
    components: { database: ok, redis: ok, models: ok, catalog: { ok: true, detail: "версия 1" } },
  };
  assert.equal(stubReason(h), null);
  assert.deepEqual(notReadyReasons(h), []);
});

test("готова, но модели с пояснением — заглушка, текст бэкенда как есть", () => {
  const h = {
    ready: true,
    components: { database: ok, redis: ok, models: { ok: true, detail: FALLBACK }, catalog: ok },
  };
  assert.equal(stubReason(h), FALLBACK);
});

test("не готова — не заглушка, а список причин без пустых", () => {
  const h = {
    ready: false,
    components: {
      database: ok,
      redis: { ok: false, detail: null },
      models: { ok: false, detail: "Модели ещё загружаются." },
      catalog: { ok: false, detail: "Каталог КТРУ не загружен." },
    },
  };
  assert.equal(stubReason(h), null);
  assert.deepEqual(notReadyReasons(h), ["Модели ещё загружаются.", "Каталог КТРУ не загружен."]);
});

test("ответа ещё нет", () => {
  assert.equal(stubReason(undefined), null);
  assert.deepEqual(notReadyReasons(undefined), []);
});
