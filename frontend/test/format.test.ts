// Тесты чистых функций форматирования. Запуск: npm test (встроенный в Node раннер, без зависимостей).
import assert from "node:assert/strict";
import { test } from "node:test";
import { bandOf, decimal, pct } from "../src/lib/format.ts";

const T = { green: 0.85, yellow: 0.5 };

test("проценты округляются вниз: жёлтая 0.846 не выглядит как 85%", () => {
  assert.equal(pct(0.846), "84%");
  assert.equal(bandOf(0.846, T), "yellow");
  assert.equal(pct(0.8499), "84%");
  assert.equal(pct(0.999), "99%");
});

test("ровные пороги не теряют процент из-за двоичной дроби", () => {
  assert.equal(pct(0.85), "85%");
  assert.equal(pct(0.57), "57%");
  assert.equal(pct(0.7), "70%");
  assert.equal(pct(0.29), "29%");
  assert.equal(pct(1), "100%");
  assert.equal(pct(0), "0%");
});

test("число на плашке не выше порога, если позиция ниже порога", () => {
  for (let i = 0; i <= 1000; i++) {
    const v = i / 1000;
    const shown = Number.parseInt(pct(v), 10) / 100;
    if (bandOf(v, T) !== "green") assert.ok(shown < T.green, `${v} показано как ${pct(v)}`);
    if (bandOf(v, T) === "red") assert.ok(shown < T.yellow, `${v} показано как ${pct(v)}`);
  }
});

test("пустое значение", () => {
  assert.equal(pct(null), "—");
  assert.equal(pct(undefined), "—");
});

test("дроби пишутся с запятой и нужным числом знаков", () => {
  assert.equal(decimal(0.06, 3), "0,060");
  assert.equal(decimal(0.031, 3), "0,031");
  assert.equal(decimal(0.05, 2), "0,05");
});
