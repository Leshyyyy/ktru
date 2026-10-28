import {
  Alert,
  Badge,
  Card,
  Group,
  Progress,
  SimpleGrid,
  Skeleton,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from "@mantine/core";
import { API_MODE } from "../api/client";
import { useQuality, useStats } from "../api/hooks";
import type { QualityRun } from "../api/types";
import { dateTime, duration, pct } from "../lib/format";

/** Метрики и цели — из docs/analytics/09-data-quality.md. Критерии организаторов помечены. */
const METRICS: {
  key: keyof NonNullable<QualityRun["metrics"]>;
  label: string;
  target: number;
  lowerIsBetter?: boolean;
  official?: boolean;
  hint: string;
}[] = [
  {
    key: "top1_full_code",
    label: "Точность, полный код КТРУ",
    target: 0.85,
    official: true,
    hint: "Доля позиций, где код на первом месте совпал с эталоном.",
  },
  {
    key: "attributes_accuracy",
    label: "Извлечение атрибутов",
    target: 0.9,
    official: true,
    hint: "Наименование, бренд и модель после нормализации.",
  },
  { key: "top1_okpd2", label: "Точность до ОКПД2", target: 0.92, hint: "Совпала часть кода до дефиса." },
  { key: "top3", label: "Эталон в топ-3", target: 0.95, hint: "Правильный код есть среди трёх вариантов." },
  {
    key: "ece",
    label: "Ошибка калибровки (ECE)",
    target: 0.05,
    lowerIsBetter: true,
    hint: "Насколько заявленные проценты расходятся с фактической точностью. Чем меньше, тем честнее проценты.",
  },
  {
    key: "fast_path_share",
    label: "Доля быстрого пути",
    target: 0.8,
    hint: "Позиции, которым не понадобился реранкер.",
  },
];

export function QualityPage() {
  const quality = useQuality();
  const stats = useStats();
  const run = quality.data?.test ?? quality.data?.dev;

  return (
    <Stack gap="lg">
      <div>
        <Title order={2}>Качество</Title>
        <Text c="dimmed" mt={4}>
          Замеры на размеченном наборе. Цифры для защиты берём только с закрытой части (test). Подтверждённые ответы
          пользователей при замере отключены, чтобы не завышать результат.
        </Text>
      </div>

      {API_MODE === "memory" && (
        <Alert color="yellow" variant="light">
          Это демо-цифры, а не результат модели: настоящие появятся, когда ML-конвейер прогонят на размеченном наборе.
        </Alert>
      )}
      {quality.isLoading && <Skeleton h={220} />}
      {quality.isError && <Alert color="red">{(quality.error as Error).message}</Alert>}
      {quality.data && !run && <Alert color="gray">Оценка ещё не запускалась.</Alert>}

      {run?.metrics && (
        <Card>
          <Group justify="space-between" mb="md">
            <Group gap="xs">
              <Text fw={600}>Последний прогон</Text>
              <Badge variant="light">{run.split === "test" ? "test (контрольный)" : "dev"}</Badge>
            </Group>
            <Text size="sm" c="dimmed">
              {run.metrics.samples} позиций · {dateTime(run.created_at)}
            </Text>
          </Group>
          <SimpleGrid cols={{ base: 1, sm: 2, md: 3 }} spacing="lg">
            {METRICS.map((m) => {
              const v = run.metrics?.[m.key] as number | undefined;
              if (v == null) return null;
              const ok = m.lowerIsBetter ? v <= m.target : v >= m.target;
              return (
                <Tooltip key={m.key} label={m.hint}>
                  <Stack gap={4}>
                    <Group justify="space-between" wrap="nowrap">
                      <Text size="sm" component="div">
                        {m.label}
                        {m.official && (
                          <Badge ml={6} size="xs" variant="outline">
                            критерий ТЗ
                          </Badge>
                        )}
                      </Text>
                    </Group>
                    <Group gap="xs" align="baseline">
                      <Text fz={26} fw={650} className="tabular" c={ok ? undefined : "red"}>
                        {m.lowerIsBetter ? v.toFixed(3) : pct(v)}
                      </Text>
                      <Text size="sm" c={ok ? "var(--k-hi-ink)" : "var(--k-lo-ink)"}>
                        {ok ? "✓" : "✗"} цель {m.lowerIsBetter ? `≤ ${m.target}` : `≥ ${pct(m.target)}`}
                      </Text>
                    </Group>
                    {!m.lowerIsBetter && (
                      <Progress value={v * 100} color={ok ? "var(--k-hi)" : "var(--k-lo)"} size="sm" />
                    )}
                  </Stack>
                </Tooltip>
              );
            })}
          </SimpleGrid>
          {run.metrics.seconds_per_1000 != null && (
            <Text size="sm" mt="lg">
              Скорость: 1000 позиций за <b>{duration(run.metrics.seconds_per_1000)}</b>{" "}
              <Text span c={run.metrics.seconds_per_1000 < 300 ? "var(--k-hi-ink)" : "var(--k-lo-ink)"}>
                (по ТЗ: меньше 5 минут)
              </Text>
            </Text>
          )}
        </Card>
      )}

      {stats.data && (
        <Card>
          <Text fw={600} mb="md">
            Работа системы
          </Text>
          <SimpleGrid cols={{ base: 2, sm: 4 }}>
            <Stat label="Задач" value={String(stats.data.jobs_total)} />
            <Stat label="Позиций обработано" value={String(stats.data.items_total)} />
            <Stat
              label="Исправлено среди «зелёных»"
              value={stats.data.green_correction_rate == null ? "нет данных" : pct(stats.data.green_correction_rate)}
              hint="Доля исправленных среди зелёных позиций, которые человек проверил вручную. Показывает, можно ли доверять массовому подтверждению."
            />
            <Stat label="Позиций с ошибкой" value={pct(stats.data.failed_items_share)} />
          </SimpleGrid>
          {stats.data.recent_errors.length > 0 && (
            <Table mt="lg" fz="sm">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Последние ошибки</Table.Th>
                  <Table.Th w={160}>Когда</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {stats.data.recent_errors.slice(0, 5).map((e, i) => (
                  <Table.Tr key={i}>
                    <Table.Td>{e.error}</Table.Td>
                    <Table.Td>{dateTime(e.at)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </Card>
      )}
    </Stack>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  const body = (
    <div>
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      <Text fz={22} fw={650} className="tabular">
        {value}
      </Text>
    </div>
  );
  return hint ? <Tooltip label={hint}>{body}</Tooltip> : body;
}
