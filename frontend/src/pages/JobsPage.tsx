import {
  ActionIcon,
  Alert,
  Badge,
  Box,
  Button,
  Card,
  Center,
  Group,
  Menu,
  Pagination,
  Progress,
  ScrollArea,
  Skeleton,
  Stack,
  Table,
  Tabs,
  Text,
  Title,
  Tooltip,
} from "@mantine/core";
import { modals } from "@mantine/modals";
import { notifications } from "@mantine/notifications";
import { IconDots, IconFileUpload, IconListCheck, IconPackages, IconPlayerStop, IconTrash } from "@tabler/icons-react";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { isActive, useBatches, useCancelJob, useDeleteJob, useItems, useJobs } from "../api/hooks";
import type { Batch, Job } from "../api/types";
import { BatchStatusBadge, JobStatusBadge } from "../components/badges";
import { dateTime, plural, pct } from "../lib/format";
import { useThresholds } from "../components/badges";

export function JobsPage() {
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") === "batches" ? "batches" : "jobs";
  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={2}>Задачи</Title>
          <Text c="dimmed" mt={4}>
            Обработка идёт на сервере: вкладку можно закрыть и вернуться к результату позже.
          </Text>
        </div>
        <Button component={Link} to="/" leftSection={<IconFileUpload size={16} />}>
          Загрузить файл
        </Button>
      </Group>

      <Tabs
        value={tab}
        onChange={(v) => {
          const n = new URLSearchParams(sp);
          if (v === "batches") n.set("tab", "batches");
          else n.delete("tab");
          setSp(n, { replace: true });
        }}
      >
        <Tabs.List mb="md">
          <Tabs.Tab value="jobs" leftSection={<IconListCheck size={16} />}>
            Задачи
          </Tabs.Tab>
          <Tabs.Tab value="batches" leftSection={<IconPackages size={16} />}>
            Комплекты
          </Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="jobs">
          <JobsList />
        </Tabs.Panel>
        <Tabs.Panel value="batches">
          <BatchesList />
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}

function JobsList() {
  const [page, setPage] = useState(1);
  const { data, isLoading, isError, error } = useJobs(page);
  const navigate = useNavigate();

  return (
    <Stack gap="lg">
      {isError && <Alert color="red">{(error as Error).message}</Alert>}
      <Card p={0}>
        <ScrollArea type="auto">
          <Table highlightOnHover verticalSpacing="sm" horizontalSpacing="lg" miw={900}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Файл</Table.Th>
                <Table.Th w={170}>Статус</Table.Th>
                <Table.Th w={320}>Прогресс</Table.Th>
                <Table.Th w={150}>Создана</Table.Th>
                <Table.Th w={170} />
                <Table.Th w={50} />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {isLoading &&
                Array.from({ length: 3 }).map((_, i) => (
                  <Table.Tr key={i}>
                    <Table.Td colSpan={6}>
                      <Skeleton h={22} />
                    </Table.Td>
                  </Table.Tr>
                ))}
              {data?.items.map((job) => (
                <Table.Tr key={job.job_id} className="row-clickable" onClick={() => navigate(`/jobs/${job.job_id}`)}>
                  <Table.Td style={{ overflowWrap: "anywhere" }}>
                    <Group gap={6} wrap="nowrap" align="center">
                      <Text fw={500} lineClamp={1}>
                        {job.filename}
                      </Text>
                      {job.batch_id && <BatchLink batchId={job.batch_id} onClick={(e) => e.stopPropagation()} />}
                    </Group>
                    <Text size="xs" c="dimmed">
                      {job.total_items
                        ? `${job.total_items} ${plural(job.total_items, "позиция", "позиции", "позиций")}`
                        : "позиции считаются"}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <JobStatusBadge status={job.status} />
                  </Table.Td>
                  <Table.Td>
                    <JobProgress job={job} />
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" className="tabular">
                      {dateTime(job.created_at)}
                    </Text>
                  </Table.Td>
                  <Table.Td onClick={(e) => e.stopPropagation()}>
                    <NextStep job={job} />
                  </Table.Td>
                  <Table.Td onClick={(e) => e.stopPropagation()}>
                    <JobMenu job={job} />
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </ScrollArea>
        {data && data.items.length === 0 && (
          <Center py={60}>
            <Stack align="center" gap="xs">
              <Text fw={500}>Задач пока нет</Text>
              <Text size="sm" c="dimmed">
                Загрузите первую спецификацию, и она появится здесь.
              </Text>
              <Button component={Link} to="/" variant="light" mt="xs">
                Загрузить файл
              </Button>
            </Stack>
          </Center>
        )}
      </Card>
      {data && data.total > data.page_size && (
        <Group justify="center">
          <Pagination value={page} onChange={setPage} total={Math.ceil(data.total / data.page_size)} />
        </Group>
      )}
    </Stack>
  );
}

/** Бейдж «комплект» у задачи из комплекта — ведёт на страницу комплекта. */
export function BatchLink({ batchId, onClick }: { batchId: string; onClick?: (e: React.MouseEvent) => void }) {
  return (
    <Tooltip label="Задача входит в комплект. Открыть комплект">
      <Badge
        component={Link}
        to={`/batches/${batchId}`}
        variant="light"
        radius="sm"
        size="sm"
        leftSection={<IconPackages size={12} />}
        onClick={onClick}
        styles={{ root: { cursor: "pointer", flex: "none" } }}
      >
        комплект
      </Badge>
    </Tooltip>
  );
}

function BatchesList() {
  const [page, setPage] = useState(1);
  const { data, isLoading, isError, error } = useBatches(page);
  const navigate = useNavigate();

  return (
    <Stack gap="lg">
      {isError && <Alert color="red">{(error as Error).message}</Alert>}
      <Card p={0}>
        <ScrollArea type="auto">
          <Table highlightOnHover verticalSpacing="sm" horizontalSpacing="lg" miw={900}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Комплект</Table.Th>
                <Table.Th w={160}>Статус</Table.Th>
                <Table.Th w={320}>Прогресс</Table.Th>
                <Table.Th w={220}>Сводка</Table.Th>
                <Table.Th w={150}>Создан</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {isLoading &&
                Array.from({ length: 3 }).map((_, i) => (
                  <Table.Tr key={i}>
                    <Table.Td colSpan={5}>
                      <Skeleton h={22} />
                    </Table.Td>
                  </Table.Tr>
                ))}
              {data?.items.map((b) => (
                <Table.Tr key={b.batch_id} className="row-clickable" onClick={() => navigate(`/batches/${b.batch_id}`)}>
                  <Table.Td style={{ overflowWrap: "anywhere" }}>
                    <Group gap={6} align="center">
                      <IconPackages size={18} color="var(--k-primary)" />
                      <div>
                        <Text fw={500} lineClamp={1}>
                          {batchTitle(b)}
                        </Text>
                        <Text size="xs" c="dimmed">
                          {b.jobs.length} {plural(b.jobs.length, "файл", "файла", "файлов")} ·{" "}
                          {b.total_items
                            ? `${b.total_items} ${plural(b.total_items, "позиция", "позиции", "позиций")}`
                            : "позиции считаются"}
                        </Text>
                      </div>
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <BatchStatusBadge status={b.status} />
                  </Table.Td>
                  <Table.Td>
                    <BatchProgress batch={b} />
                  </Table.Td>
                  <Table.Td>
                    <BatchZones summary={b.summary} />
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" className="tabular">
                      {dateTime(b.created_at)}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </ScrollArea>
        {data && data.items.length === 0 && (
          <Center py={60}>
            <Stack align="center" gap="xs">
              <Text fw={500}>Комплектов пока нет</Text>
              <Text size="sm" c="dimmed">
                Загрузите несколько спецификаций сразу, и они появятся здесь как комплект.
              </Text>
              <Button component={Link} to="/" variant="light" mt="xs">
                Загрузить файлы
              </Button>
            </Stack>
          </Center>
        )}
      </Card>
      {data && data.total > data.page_size && (
        <Group justify="center">
          <Pagination value={page} onChange={setPage} total={Math.ceil(data.total / data.page_size)} />
        </Group>
      )}
    </Stack>
  );
}

export const batchTitle = (b: { name?: string | null; jobs: Job[] }) =>
  b.name?.trim() || (b.jobs.length ? b.jobs.map((j) => j.filename).join(", ") : "Комплект");

function BatchProgress({ batch }: { batch: Batch }) {
  const done = batch.processed_items + batch.failed_items;
  return (
    <Stack gap={4}>
      <Progress
        value={batch.progress * 100}
        size="sm"
        animated={batch.status === "processing"}
        color={batch.status === "cancelled" ? "gray" : batch.failed_items ? "orange" : undefined}
      />
      <Text size="xs" c="dimmed" className="tabular">
        {batch.total_items ? `${done} из ${batch.total_items}` : "позиции считаются"}
      </Text>
    </Stack>
  );
}

function BatchZones({ summary }: { summary: Batch["summary"] }) {
  const t = useThresholds();
  const zones: { n: number; color: string; label: string; hint: string }[] = [
    { n: summary.green ?? 0, color: "var(--k-hi)", label: "зелёные", hint: `уверенность от ${pct(t.green)}` },
    {
      n: summary.yellow ?? 0,
      color: "var(--k-mid)",
      label: "жёлтые",
      hint: `уверенность ${pct(t.yellow)}–${pct(t.green)}`,
    },
    { n: summary.red ?? 0, color: "var(--k-lo)", label: "красные", hint: `уверенность ниже ${pct(t.yellow)}` },
  ];
  return (
    <Group gap={8} wrap="wrap">
      {zones.map((z) => (
        <Tooltip key={z.label} label={z.hint}>
          <Group gap={4} wrap="nowrap">
            <Box w={8} h={8} style={{ borderRadius: 2, background: z.color, flex: "none" }} aria-hidden />
            <Text size="xs" className="tabular">
              {z.n}
            </Text>
          </Group>
        </Tooltip>
      ))}
      {(summary.missing_required ?? 0) > 0 && (
        <Text size="xs" c="var(--k-lo-ink)" className="tabular">
          обяз. не заполнено: {summary.missing_required}
        </Text>
      )}
    </Group>
  );
}

function JobProgress({ job }: { job: Job }) {
  const done = job.processed_items + job.failed_items;
  const s = job.summary;
  const zones = [
    { n: s.green ?? 0, color: "var(--k-hi)", word: ["зелёная", "зелёные", "зелёных"] as const },
    { n: s.yellow ?? 0, color: "var(--k-mid)", word: ["жёлтая", "жёлтые", "жёлтых"] as const },
    { n: s.red ?? 0, color: "var(--k-lo)", word: ["красная", "красные", "красных"] as const },
  ];
  return (
    <Stack gap={4}>
      <Progress
        value={job.progress * 100}
        size="sm"
        animated={isActive(job.status)}
        color={job.status === "cancelled" ? "gray" : job.failed_items ? "orange" : undefined}
      />
      <Text size="xs" c="dimmed" className="tabular">
        {job.total_items ? `${done} из ${job.total_items}` : "позиции считаются"}
      </Text>
      {zones.some((z) => z.n > 0) && (
        <Group gap={8} wrap="wrap">
          {zones.map((z) => (
            <Group key={z.word[2]} gap={4} wrap="nowrap">
              <Box w={8} h={8} style={{ borderRadius: 2, background: z.color, flex: "none" }} aria-hidden />
              <Text size="xs" c="dimmed" className="tabular">
                {z.n} {plural(z.n, z.word[0], z.word[1], z.word[2])}
              </Text>
            </Group>
          ))}
        </Group>
      )}
    </Stack>
  );
}

/** Следующий шаг по задаче: проверить спорные позиции, если они есть, иначе — просто открыть результат. */
function NextStep({ job }: { job: Job }) {
  const disputed = useItems(job.job_id, { status: "needs_review", page: 1, page_size: 1 }).data?.total ?? 0;
  if (disputed > 0)
    return (
      <Button
        component={Link}
        to={`/jobs/${job.job_id}?status=needs_review&open=first`}
        variant="light"
        size="xs"
        leftSection={<IconListCheck size={14} />}
      >
        Проверить · {disputed}
      </Button>
    );
  return (
    <Button component={Link} to={`/jobs/${job.job_id}`} variant="subtle" size="xs" color="gray">
      Открыть
    </Button>
  );
}

function JobMenu({ job }: { job: Job }) {
  const cancel = useCancelJob();
  const del = useDeleteJob();
  const onError = (e: Error) => notifications.show({ color: "red", message: e.message });

  const confirmDelete = () =>
    modals.openConfirmModal({
      title: "Удалить задачу?",
      children: (
        <Text size="sm">
          Файл «{job.filename}», результаты и ваши правки будут удалены. Подтверждённые коды останутся в эталонных
          примерах системы.
        </Text>
      ),
      labels: { confirm: "Удалить", cancel: "Отмена" },
      confirmProps: { color: "red" },
      onConfirm: () => del.mutate(job.job_id, { onError }),
    });

  return (
    <Menu position="bottom-end" withinPortal>
      <Menu.Target>
        <ActionIcon variant="subtle" color="gray" aria-label="Действия с задачей">
          <IconDots size={18} />
        </ActionIcon>
      </Menu.Target>
      <Menu.Dropdown>
        {isActive(job.status) && (
          <Menu.Item leftSection={<IconPlayerStop size={16} />} onClick={() => cancel.mutate(job.job_id, { onError })}>
            Остановить обработку
          </Menu.Item>
        )}
        <Menu.Item color="red" leftSection={<IconTrash size={16} />} onClick={confirmDelete}>
          Удалить
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}
