import { Badge, Box, Group, Text, Tooltip } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";
import type { ItemFlag, ItemStatus, JobStatus } from "../api/types";
import { useConfig } from "../api/hooks";
import { BAND, bandOf, FLAG, ITEM_STATUS, JOB_STATUS, pct } from "../lib/format";

const DEFAULT_T = { green: 0.85, yellow: 0.5 };

export function useThresholds() {
  return useConfig().data?.thresholds ?? DEFAULT_T;
}

/** Уверенность модели: цвет зоны + число. Пороги берём из /config, а не зашиваем во фронт. */
export function ConfidenceBadge({
  value,
  size = "md",
}: {
  value: number | null | undefined;
  size?: "sm" | "md" | "lg";
}) {
  const t = useThresholds();
  const band = bandOf(value, { green: t.green ?? DEFAULT_T.green, yellow: t.yellow ?? DEFAULT_T.yellow });
  if (!band)
    return (
      <Text c="dimmed" size="sm">
        —
      </Text>
    );
  const b = BAND[band];
  return (
    <Tooltip label={`${b.label}: ${b.hint.toLowerCase()}. Это оценка вероятности того, что код верный.`}>
      <Badge
        color={b.color}
        variant="light"
        size={size}
        className="tabular"
        leftSection={<Box w={7} h={7} style={{ borderRadius: 7, background: `var(--mantine-color-${b.color}-6)` }} />}
        miw={64}
      >
        {pct(value)}
      </Badge>
    </Tooltip>
  );
}

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const s = JOB_STATUS[status];
  return (
    <Badge color={s.color} variant="light">
      {s.label}
    </Badge>
  );
}

export function ItemStatusBadge({ status }: { status: ItemStatus }) {
  const s = ITEM_STATUS[status];
  return (
    <Badge color={s.color} variant={status === "confirmed" || status === "corrected" ? "filled" : "light"} size="sm">
      {s.label}
    </Badge>
  );
}

export function FlagsIcon({ flags }: { flags: ItemFlag[] | undefined }) {
  if (!flags?.length) return null;
  return (
    <Tooltip label={flags.map((f) => FLAG[f].label).join("; ")}>
      <Group gap={2} c="orange.7" wrap="nowrap" aria-label="Есть предупреждения">
        <IconAlertTriangle size={16} />
      </Group>
    </Tooltip>
  );
}
