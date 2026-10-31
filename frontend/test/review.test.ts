// Плитка «Проверено»: какой список открыть. Запуск: npm test.
import assert from "node:assert/strict";
import { test } from "node:test";
import { isReviewed, reviewedStatus } from "../src/lib/review.ts";

test("если есть только исправленные, плитка открывает их, а не пустой список подтверждённых", () => {
  assert.equal(reviewedStatus({ confirmed: 0, corrected: 2 }), "corrected");
});

test("если есть подтверждённые, начинаем с них", () => {
  assert.equal(reviewedStatus({ confirmed: 3, corrected: 2 }), "confirmed");
  assert.equal(reviewedStatus({ confirmed: 1, corrected: 0 }), "confirmed");
  assert.equal(reviewedStatus({ confirmed: 0, corrected: 0 }), "confirmed");
});

test("проверенными считаются только подтверждённые и исправленные", () => {
  assert.ok(isReviewed("confirmed"));
  assert.ok(isReviewed("corrected"));
  assert.ok(!isReviewed("needs_review"));
  assert.ok(!isReviewed(undefined));
});
