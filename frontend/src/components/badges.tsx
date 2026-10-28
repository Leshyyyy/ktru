import { Group, Text, Tooltip } from "@mantine/core";
import { IconAlertTriangle, IconCheck } from "@tabler/icons-react";
import type { ItemFlag, ItemStatus, JobStatus } from "../api/types";
import { useConfig } from "../api/hooks";
import { BAND, bandOf, FLAG, ITEM_STATUS, JOB_STATUS, pct } from "../lib/format";
import classes from "./badges.module.css";

const DEFAULT_T = { green: 0.85, yellow: 0.5 };

export function useThresholds() {
  const t = useConfig().data?.thresholds;
  return { green: t?.green ?? DEFAULT_T.green, yellow: t?.yellow ?? DEFAULT_T.yellow };
}

/**
 * Уверенность модели: число и полоска в цвете зоны. Пороги — из /config, а не зашиты во фронт.
 * Цвет не единственный носитель смысла: число видно всегда.
 */
export function ConfidenceBadge({
  value,
  size = "md",
}: {
  value: number | null | undefined;
  size?: "sm" | "md" | "lg";
}) {
  const t = useThresholds();
  const band = bandOf(value, t);
  if (!band || value == null)
    return (
      <Text c="dimmed" size="sm">
        нет оценки
      </Text>
    );
  const b = BAND[band];
  return (
    <Tooltip
      label={`${b.label}: ${b.hint.toLowerCase()}. Из 10 позиций с такой уверенностью верны примерно ${Math.round(value * 10)}.`}
    >
      <span className={classes.conf} data-band={band} data-size={size} style={{ "--v": value * 100 } as never}>
        {pct(value)}
        <i aria-hidden />
      </span>
    </Tooltip>
  );
}

/** Тон плашки статуса задачи: те же цвета, что у статусов позиций, текст — тёмным оттенком (контраст ≥ 4.5:1). */
const JOB_TONE: Record<JobStatus, "neutral" | "busy" | "ok" | "warn" | "bad"> = {
  queued: "neutral",
  parsing: "busy",
  processing: "busy",
  done: "ok",
  done_with_errors: "warn",
  failed: "bad",
  cancelled: "neutral",
};

export function JobStatusBadge({ status }: { status: JobStatus }) {
  return (
    <span className={classes.st} data-tone={JOB_TONE[status]}>
      {JOB_STATUS[status].label}
    </span>
  );
}

/** Статус позиции в стиле макета: мягкая плашка с подписью; у «Подтверждена» — галочка. */
export function ItemStatusBadge({ status }: { status: ItemStatus }) {
  return (
    <span className={classes.st} data-status={status}>
      {status === "confirmed" && <IconCheck size={12} stroke={2.5} />}
      {ITEM_STATUS[status].label}
    </span>
  );
}

/** Предупреждения позиции: подпись целиком (флаг без текста непонятен). */
export function FlagChips({ flags }: { flags: ItemFlag[] | undefined }) {
  if (!flags?.length) return null;
  return (
    <Group gap={4} mt={4}>
      {flags.map((f) => (
        <Tooltip key={f} label={FLAG[f].hint}>
          <span className={classes.flag}>
            <IconAlertTriangle size={11} stroke={2.2} />
            {FLAG[f].short}
          </span>
        </Tooltip>
      ))}
    </Group>
  );
}

/** Значок предупреждения для узких мест; полная подпись — во всплывающей подсказке. */
export function FlagsIcon({ flags }: { flags: ItemFlag[] | undefined }) {
  if (!flags?.length) return null;
  return (
    <Tooltip label={flags.map((f) => FLAG[f].label).join("; ")}>
      <Group gap={2} c="var(--k-flag)" wrap="nowrap" aria-label="Есть предупреждения">
        <IconAlertTriangle size={16} />
      </Group>
    </Tooltip>
  );
}

/** «+N таких же» — та же позиция (наименование, бренд, модель) встречается в файле ещё N раз. */
export function GroupBadge({ size, onClick }: { size: number; onClick?: () => void }) {
  if (size <= 1) return null;
  return (
    <Tooltip label="Показать все позиции с тем же товаром">
      <button
        type="button"
        className={classes.grp}
        onClick={(e) => {
          e.stopPropagation();
          onClick?.();
        }}
      >
        +{size - 1} таких же
      </button>
    </Tooltip>
  );
}

/**
 * Код КТРУ: часть ОКПД2 жирная, суффикс светлее — соседние коды часто отличаются только хвостом,
 * и так это сразу видно.
 */
export function KtruCode({ code, size = "md" }: { code: string | null | undefined; size?: "md" | "lg" }) {
  if (!code) return <Text c="dimmed">нет кода</Text>;
  const i = code.indexOf("-");
  const head = i >= 0 ? code.slice(0, i) : code;
  const tail = i >= 0 ? code.slice(i) : "";
  return (
    <span className={classes.code} data-size={size}>
      <b>{head}</b>
      <span>{tail}</span>
    </span>
  );
}
