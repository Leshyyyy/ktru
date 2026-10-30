import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Progress,
  SimpleGrid,
  Skeleton,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from "@mantine/core";
import { modals } from "@mantine/modals";
import { IconPlayerPlay, IconTargetArrow } from "@tabler/icons-react";
import { API_MODE } from "../api/client";
import { useQuality, useRunQuality, useStats, type EvalSplit } from "../api/hooks";
import type { QualityRun } from "../api/types";
import { dateTime, duration, pct, plural } from "../lib/format";

/**
 * Метрики и цели — из docs/analytics/09-data-quality.md. Критерии организаторов помечены.
 * Метрика без цели (target) показывается справочно.
 */
const METRICS: {
  key: string;
  label: string;
  target?: number;
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
    hint: "Наименование, бренд и модель совпали с разметкой после нормализации.",
  },
  {
    key: "top1_any_valid",
    label: "Точность с допустимыми кодами",
    hint: "Код на первом месте совпал с эталоном или с другим кодом, который разметчик тоже признал верным.",
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
    hint: "Позиции, по которым хватило поиска по каталогу без дополнительной проверки вариантов.",
  },
];

const SPLIT: Record<EvalSplit, { title: string; hint: string }> = {
  test: {
    title: "Контрольный набор (test)",
    hint: "Закрытая часть разметки: под неё систему не настраивали. Цифры для защиты берём отсюда.",
  },
  dev: {
    title: "Рабочий набор (dev)",
    hint: "На нём команда проверяет правки. Цифры могут быть немного выше, чем на контрольном.",
  },
};

const FIELD: Record<string, string> = { name: "наименование", brand: "бренд", model: "модель" };

/** Метрики ответа: в контракте — основные, а evaluate() кладёт и дополнительные (top1_any_valid, по полям). */
type Metrics = Record<string, unknown>;
const num = (m: Metrics, key: string) => (typeof m[key] === "number" ? (m[key] as number) : null);

export function QualityPage() {
  const quality = useQuality();
  const stats = useStats();
  const runQuality = useRunQuality();
  const runs = (["test", "dev"] as const).map((s) => quality.data?.[s]).filter((r): r is QualityRun => !!r);
  const running = (s: EvalSplit) => quality.data?.[s]?.status === "running";

  const start = (split: EvalSplit) => runQuality.mutate(split);
  // Контрольный набор запускают редко: если подбирать систему под него, цифры перестанут быть честными.
  const startTest = () =>
    modals.openConfirmModal({
      title: "Запустить контрольный замер?",
      children: (
        <Text size="sm">
          Контрольный набор запускают перед сдачей, чтобы получить честные цифры. Для проверки правок используйте
          рабочий набор.
        </Text>
      ),
      labels: { confirm: "Запустить", cancel: "Отмена" },
      onConfirm: () => start("test"),
    });

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-start" wrap="wrap" gap="md">
        <div style={{ flex: "1 1 420px" }}>
          <Title order={2}>Качество</Title>
          <Text c="dimmed" mt={4}>
            Замеры на размеченном наборе: система подбирает коды для позиций с известным ответом, и мы считаем, как
            часто она права. Подтверждённые ответы пользователей при замере отключены, чтобы не завышать результат.
          </Text>
        </div>
        <Group gap="sm">
          <Button
            variant="default"
            leftSection={<IconTargetArrow size={16} />}
            onClick={startTest}
            disabled={running("test")}
          >
            Контрольный замер
          </Button>
          <Button
            leftSection={<IconPlayerPlay size={16} />}
            onClick={() => start("dev")}
            loading={runQuality.isPending && runQuality.variables === "dev"}
            disabled={running("dev")}
          >
            Запустить оценку
          </Button>
        </Group>
      </Group>

      {API_MODE === "memory" && (
        <Alert color="yellow" variant="light">
          Это демо-цифры, а не результат модели: настоящие появятся, когда ML-конвейер прогонят на размеченном наборе.
        </Alert>
      )}
      {runQuality.isError && (
        <Alert color="red" title="Оценку не удалось запустить" withCloseButton onClose={() => runQuality.reset()}>
          {(runQuality.error as Error).message}
        </Alert>
      )}
      {quality.isLoading && <Skeleton h={220} />}
      {quality.isError && <Alert color="red">{(quality.error as Error).message}</Alert>}
      {quality.data && runs.length === 0 && (
        <Card>
          <Text fw={600}>Оценка ещё не запускалась</Text>
          <Text size="sm" c="dimmed" mt={4}>
            Нажмите «Запустить оценку»: прогон рабочего набора занимает минуту-две, страницу можно не держать открытой.
          </Text>
        </Card>
      )}

      {runs.map((run) => (
        <RunCard key={run.run_id} run={run} />
      ))}

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
          {stats.data.avg_seconds_per_1000 != null && (
            <Text size="sm" mt="lg">
              Средняя скорость: 1000 позиций за{" "}
              <Text span inherit fw={600}>
                {duration(stats.data.avg_seconds_per_1000)}
              </Text>{" "}
              <Text span c={stats.data.avg_seconds_per_1000 < 300 ? "var(--k-hi-ink)" : "var(--k-lo-ink)"}>
                (по ТЗ: меньше 5 минут)
              </Text>
            </Text>
          )}
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

/** Последний прогон одного набора: идёт, не удался или готов с метриками. */
function RunCard({ run }: { run: QualityRun }) {
  const s = SPLIT[run.split];
  const m = (run.metrics ?? {}) as Metrics;
  const samples = num(m, "samples");
  const seconds = num(m, "seconds_per_1000");
  const byField = (m.attributes_by_field ?? {}) as Record<string, number>;
  const error = typeof run.config?.error === "string" ? run.config.error : null;

  return (
    <Card>
      <Group justify="space-between" mb="md" wrap="wrap" gap="xs">
        <Tooltip label={s.hint}>
          <Text fw={600}>{s.title}</Text>
        </Tooltip>
        <Text size="sm" c="dimmed">
          {samples != null && `${samples} ${plural(samples, "позиция", "позиции", "позиций")}, `}
          {run.status === "running" ? "запущена " : ""}
          {dateTime(run.created_at)}
        </Text>
      </Group>

      {run.status === "running" && (
        <Group gap="sm">
          <Loader size="sm" />
          <Text size="sm">Идёт оценка, обычно это минута-две. Результат появится здесь сам.</Text>
        </Group>
      )}
      {run.status === "failed" && (
        <Alert color="red" variant="light">
          {error ?? "Оценка не выполнена. Запустите её ещё раз."}
        </Alert>
      )}

      {run.status === "done" && (
        <>
          <SimpleGrid cols={{ base: 1, sm: 2, md: 3 }} spacing="lg">
            {METRICS.map((d) => {
              const v = num(m, d.key);
              if (v == null) return null;
              const ok = d.target == null ? null : d.lowerIsBetter ? v <= d.target : v >= d.target;
              return (
                <Tooltip key={d.key} label={d.hint}>
                  <Stack gap={4}>
                    <Text size="sm" component="div">
                      {d.label}
                      {d.official && (
                        <Badge ml={8} size="md" variant="outline" tt="none" fw={600}>
                          критерий ТЗ
                        </Badge>
                      )}
                    </Text>
                    <Group gap="xs" align="baseline">
                      <Text fz={26} fw={600} className="tabular" c={ok === false ? "var(--k-lo-ink)" : undefined}>
                        {d.lowerIsBetter ? v.toFixed(3) : pct(v)}
                      </Text>
                      <Text size="sm" c={ok == null ? "dimmed" : ok ? "var(--k-hi-ink)" : "var(--k-lo-ink)"}>
                        {ok == null
                          ? "справочно"
                          : `${ok ? "✓" : "✗"} цель ${d.lowerIsBetter ? `≤ ${d.target}` : `≥ ${pct(d.target!)}`}`}
                      </Text>
                    </Group>
                    {!d.lowerIsBetter && (
                      <Progress
                        value={v * 100}
                        color={ok === false ? "var(--k-lo)" : ok ? "var(--k-hi)" : "var(--k-line-strong)"}
                        size="sm"
                      />
                    )}
                    {d.key === "attributes_accuracy" && Object.keys(byField).length > 0 && (
                      <Text size="xs" c="dimmed">
                        {Object.entries(byField)
                          .map(([f, a]) => `${FIELD[f] ?? f} ${pct(a)}`)
                          .join(", ")}
                      </Text>
                    )}
                  </Stack>
                </Tooltip>
              );
            })}
          </SimpleGrid>
          {seconds != null && (
            <Text size="sm" mt="lg">
              Скорость на этом наборе: 1000 позиций за{" "}
              <Text span inherit fw={600}>
                {duration(seconds)}
              </Text>{" "}
              <Text span c={seconds < 300 ? "var(--k-hi-ink)" : "var(--k-lo-ink)"}>
                (по ТЗ: меньше 5 минут)
              </Text>
            </Text>
          )}
        </>
      )}
    </Card>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  const body = (
    <div>
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      <Text fz={22} fw={600} className="tabular">
        {value}
      </Text>
    </div>
  );
  return hint ? <Tooltip label={hint}>{body}</Tooltip> : body;
}
