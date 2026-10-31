/**
 * Решения по позициям с возможностью «Отменить».
 *
 * В API нет отмены решения, поэтому решение отправляется не сразу, а через UNDO_MS: пока висит тост
 * «Строка 51 подтверждена · Отменить», запрос ещё не ушёл, и отмена просто его не отправляет.
 * Чтобы интерфейс не ждал, таблица и карточка сразу показывают решение как принятое (useDecisionOverlay).
 * Если пользователь уходит со страницы раньше — отложенные решения отправляются немедленно.
 */
import { Group, Progress, Text, UnstyledButton } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, unwrap } from "../api/client";
import { keys } from "../api/hooks";
import type { FinalAnswer, Item, ItemStatus } from "../api/types";
import classes from "./DecisionQueue.module.css";

export const UNDO_MS = 5000;

export type Decision =
  | { kind: "confirm"; item: Item; code: string; name: string | null }
  | { kind: "correct"; item: Item; code: string; name: string | null; reason?: string | null };

type Pending = { id: number; decision: Decision; label: string; due: number; timer: number };
type Overlay = { status: ItemStatus; final: FinalAnswer };

type Ctx = {
  decide: (d: Decision) => void;
  overlay: (itemId: string) => Overlay | null;
};

const DecisionCtx = createContext<Ctx | null>(null);

export function DecisionQueueProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<Pending[]>([]);
  const pendingRef = useRef<Pending[]>([]);
  pendingRef.current = pending;
  const seq = useRef(0);

  /**
   * keepalive — для отправки при закрытии вкладки: обычный fetch браузер обрывает вместе со страницей,
   * а запрос с keepalive доживает до сервера (тело до 64 КБ, наши — сотни байт).
   */
  const send = useCallback(
    async (p: Pending, keepalive = false) => {
      setPending((list) => list.filter((x) => x.id !== p.id));
      const d = p.decision;
      try {
        const item =
          d.kind === "confirm"
            ? await unwrap(
                api.POST("/items/{item_id}/confirm", { params: { path: { item_id: d.item.item_id } }, keepalive }),
              )
            : await unwrap(
                api.PUT("/items/{item_id}/decision", {
                  params: { path: { item_id: d.item.item_id } },
                  body: { ktru_code: d.code, reason: d.reason || null },
                  keepalive,
                }),
              );
        qc.setQueryData(keys.item(item.item_id), item);
      } catch (e) {
        notifications.show({
          color: "red",
          title: `Строка ${d.item.row_index + 1}: решение не сохранено`,
          message: (e as Error).message,
        });
      } finally {
        qc.invalidateQueries({ queryKey: ["items", d.item.job_id] });
        qc.invalidateQueries({ queryKey: keys.job(d.item.job_id) });
        qc.invalidateQueries({ queryKey: keys.item(d.item.item_id) });
      }
    },
    [qc],
  );

  const decide = useCallback(
    (decision: Decision) => {
      const id = ++seq.current;
      const row = decision.item.row_index + 1;
      const label = decision.kind === "confirm" ? `Строка ${row} подтверждена` : `Строка ${row}: код ${decision.code}`;
      // Повторное решение по той же позиции заменяет предыдущее, ещё не отправленное.
      // Таймеры заводим снаружи setPending: в StrictMode функция-обновление вызывается дважды,
      // и запрос ушёл бы два раза.
      const sameItem = (x: Pending) => x.decision.item.item_id === decision.item.item_id;
      pendingRef.current.filter(sameItem).forEach((x) => clearTimeout(x.timer));
      const p: Pending = { id, decision, label, due: Date.now() + UNDO_MS, timer: 0 };
      p.timer = window.setTimeout(() => send(p), UNDO_MS);
      setPending((list) => [...list.filter((x) => !sameItem(x)), p]);
    },
    [send],
  );

  const undo = (p: Pending) => {
    clearTimeout(p.timer);
    setPending((list) => list.filter((x) => x.id !== p.id));
  };

  // Ушли со страницы или закрыли вкладку — отправляем всё, что ждёт.
  useEffect(() => {
    const flush = () =>
      pendingRef.current.forEach((p) => {
        clearTimeout(p.timer);
        void send(p, true);
      });
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("pagehide", flush);
      flush();
    };
  }, [send]);

  const overlay = useCallback(
    (itemId: string): Overlay | null => {
      const p = pending.find((x) => x.decision.item.item_id === itemId);
      if (!p) return null;
      const d = p.decision;
      const sameAsTop = d.item.predictions[0]?.ktru_code === d.code;
      return {
        status: d.kind === "confirm" || sameAsTop ? "confirmed" : "corrected",
        final: { ktru_code: d.code, name: d.name, source: "user", confidence: null },
      };
    },
    [pending],
  );

  const value = useMemo(() => ({ decide, overlay }), [decide, overlay]);
  const last = pending.at(-1);

  return (
    <DecisionCtx.Provider value={value}>
      {children}
      {last && <UndoToast key={last.id} p={last} more={pending.length - 1} onUndo={() => undo(last)} />}
    </DecisionCtx.Provider>
  );
}

function UndoToast({ p, more, onUndo }: { p: Pending; more: number; onUndo: () => void }) {
  const [left, setLeft] = useState(p.due - Date.now());
  useEffect(() => {
    const t = setInterval(() => setLeft(p.due - Date.now()), 100);
    return () => clearInterval(t);
  }, [p.due]);
  return (
    <div className={classes.toast} role="status">
      <Group gap={18} wrap="nowrap">
        <Text size="sm">
          {p.label}
          {more > 0 && ` · ещё ${more}`}
        </Text>
        <UnstyledButton className={classes.undo} onClick={onUndo}>
          Отменить
        </UnstyledButton>
      </Group>
      <Progress
        value={Math.max(0, (left / UNDO_MS) * 100)}
        size={2}
        color="var(--k-lime)"
        mt={8}
        transitionDuration={100}
      />
    </div>
  );
}

export function useDecisions() {
  const ctx = useContext(DecisionCtx);
  if (!ctx) throw new Error("useDecisions вне DecisionQueueProvider");
  return ctx;
}

/** Позиция с учётом ещё не отправленного решения: статус и итоговый код сразу как после сохранения. */
export function useWithOverlay() {
  const { overlay } = useDecisions();
  return useCallback(
    (item: Item): Item => {
      const o = overlay(item.item_id);
      return o ? { ...item, status: o.status, final: o.final } : item;
    },
    [overlay],
  );
}
