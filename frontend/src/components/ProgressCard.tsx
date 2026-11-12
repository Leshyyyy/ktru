import { Card, Group, Progress, Stack, Text } from "@mantine/core";
import { duration } from "../lib/format";

/** Карточка прогресса обработки: «Обработано N из M», полоса, «осталось ≈ …». Общая для задачи и комплекта. */
export function ProgressCard({
  status,
  processed,
  total,
  progress,
  etaSeconds,
  labelPrefix,
  hint,
}: {
  status: "queued" | "parsing" | "processing";
  processed: number;
  total: number;
  progress: number;
  etaSeconds?: number | null;
  /** Слово перед счётом, напр. «Обработано» (задача) или «Обрабатывается» (комплект). */
  labelPrefix?: string;
  hint?: string;
}) {
  const done = processed;
  const label =
    status === "queued"
      ? "В очереди, сейчас начнётся"
      : status === "parsing"
        ? "Читаем файл и считаем позиции"
        : `${labelPrefix ?? "Обработано"} ${done} из ${total}`;
  return (
    <Card>
      <Stack gap="xs">
        <Group justify="space-between">
          <Text fw={600}>{label}</Text>
          <Text size="sm" c="dimmed" className="tabular">
            {etaSeconds != null ? `осталось ≈ ${duration(etaSeconds)}` : ""}
          </Text>
        </Group>
        <Progress value={Math.max(progress * 100, status === "processing" ? 2 : 0)} size="lg" animated striped />
        {hint && (
          <Text size="xs" c="dimmed">
            {hint}
          </Text>
        )}
      </Stack>
    </Card>
  );
}
