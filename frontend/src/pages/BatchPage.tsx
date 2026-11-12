/**
 * Страница комплекта — группы задач, созданных вместе (экраны доп. задач A).
 * Сводка по зонам и незаполненным характеристикам, таблица позиций с группировкой по файлам,
 * общая выгрузка. Признак фильтра «незаполненные обязательные» живёт в адресе страницы.
 */
import {
  Alert,
  Anchor,
  Button,
  Card,
  Center,
  Group,
  Menu,
  Progress,
  ScrollArea,
  Skeleton,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
  UnstyledButton,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { modals } from "@mantine/modals";
import { IconChevronDown, IconDownload } from "@tabler/icons-react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { api, unwrap } from "../api/client";
import type { Batch } from "../api/types";
import { useBatch, useBatchExport } from "../api/hooks";
import { BatchStatusBadge, JobStatusBadge } from "../components/badges";
import { ItemDrawer } from "../components/ItemDrawer";
import { batchTitle } from "./JobsPage";
import { dateTime, duration, plural } from "../lib/format";

export function BatchPage() {
  const { batchId = "" } = useParams();
  const { data: batch, isLoading, isError, error } = useBatch(batchId);
  if (isError)
    return (
      <Stack>
        <BackLink />
        <Alert color="red" title="Комплект не открылся">
          {(error as Error).message}
        </Alert>
      </Stack>
    );
  if (isLoading || !batch)
    return (
      <Stack>
        <Skeleton h={40} w={360} />
        <Skeleton h={90} />
        <Skeleton h={400} />
      </Stack>
    );
  return <BatchView batch={batch} />;
}

function BackLink() {
  return (
    <Text size="sm" c="var(--k-faint)">
      <Anchor component={Link} to="/jobs?tab=batches" size="sm" c="dimmed" underline="hover">
        Комплекты
      </Anchor>
    </Text>
  );
}

function BatchView({ batch }: { batch: Batch }) {
  const [sp, setSp] = useSearchParams();
  const missingOnly = sp.get("missing_required") === "true";
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [openItem, setOpenItem] = useState<{ itemId: string; jobId: string } | null>(null);
  // Задача выбранной позиции — чтобы карточка открылась для правильного каталога.
  const openItemJob = openItem ? batch.jobs.find((j) => j.job_id === openItem.jobId) : undefined;

  const jobs = batch.jobs;

  return (
    <Stack gap="lg">
      <BackLink />
      <Stack gap={6}>
        <Group justify="space-between" align="flex-start" wrap="wrap" gap="md">
          <div style={{ minWidth: 0 }}>
            <Group gap="sm" wrap="wrap">
              <Title order={2} lineClamp={2} style={{ overflowWrap: "anywhere" }}>
                {batchTitle(batch)}
              </Title>
              <BatchStatusBadge status={batch.status} />
              {batch.total_items > 0 && (
                <span className="k-pill">
                  {batch.total_items} {plural(batch.total_items, "позиция", "позиции", "позиций")} · {batch.jobs.length}{" "}
                  {plural(batch.jobs.length, "файл", "файла", "файлов")}
                </span>
              )}
            </Group>
            <Text size="sm" c="dimmed" mt={4}>
              Создан {dateTime(batch.created_at)}
              {batch.eta_seconds != null && ` · осталось ≈ ${duration(batch.eta_seconds)}`}
            </Text>
          </div>
          <BatchExport batch={batch} />
        </Group>
      </Stack>

      {batch.status === "processing" && (
        <Card>
          <Stack gap="xs">
            <Group justify="space-between">
              <Text fw={600}>
                Обрабатывается{" "}
                {batch.total_items > 0 ? `${batch.processed_items + batch.failed_items} из ${batch.total_items}` : ""}
              </Text>
            </Group>
            <Progress value={Math.max(batch.progress * 100, 2)} size="lg" animated striped />
            <Text size="xs" c="dimmed">
              Файлы комплекта обрабатываются вместе. Готовые позиции уже видны ниже.
            </Text>
          </Stack>
        </Card>
      )}

      <BatchSummary batch={batch} />

      {missingOnly && (
        <Alert color="orange" withCloseButton onClose={() => setSp({}, { replace: true })}>
          Показаны только позиции с незаполненными обязательными характеристиками.
        </Alert>
      )}

      {jobs.length === 0 ? (
        <Center py={50}>
          <Text fw={500}>{missingOnly ? "Нет позиций с незаполненными обязательными" : "Позиций пока нет"}</Text>
        </Center>
      ) : (
        <Card p={0}>
          {jobs.map((job) => (
            <BatchFileGroup
              key={job.job_id}
              job={job}
              missingOnly={missingOnly}
              open={expanded[job.job_id] ?? true}
              onToggle={() => setExpanded((e) => ({ ...e, [job.job_id]: !(e[job.job_id] ?? true) }))}
              onOpenItem={(itemId, jobId) => setOpenItem({ itemId, jobId })}
            />
          ))}
        </Card>
      )}

      {/* Отдельная карточка выбранной позиции поверх страницы — как на странице задачи. */}
      <ItemDrawer
        itemId={openItem ? openItem.itemId : null}
        job={openItemJob ?? batch.jobs[0]!}
        onClose={() => setOpenItem(null)}
        onAfterDecision={() => setOpenItem(null)}
        onGroup={(_g) => {}}
      />
    </Stack>
  );
}

/** Одна группа файла комплекта. Позиции запрашиваются отдельно от задачи — useItems нельзя в map(). */
function BatchFileGroup({
  job,
  missingOnly,
  open,
  onToggle,
  onOpenItem,
}: {
  job: Batch["jobs"][number];
  missingOnly: boolean;
  open: boolean;
  onToggle: () => void;
  onOpenItem: (itemId: string, jobId: string) => void;
}) {
  // Контракт: PageSize максимум 200. Грузим страницами по 200 и «Показать ещё» ДОБАВЛЯЕТ следующие (не заменяет).
  const PAGE = 200;
  const active = ["queued", "parsing", "processing"].includes(job.status);
  const { data, isFetching, fetchNextPage, hasNextPage } = useInfiniteQuery({
    queryKey: ["batch-items", job.job_id, missingOnly],
    queryFn: ({ pageParam = 1 }) =>
      unwrap(
        api.GET("/jobs/{job_id}/items", {
          params: {
            path: { job_id: job.job_id },
            query: { page: pageParam, page_size: PAGE, missing_required: missingOnly || undefined },
          },
        }),
      ),
    initialPageParam: 1,
    getNextPageParam: (last, all) => (last.total > all.length * PAGE ? all.length + 1 : undefined),
    // Во время обработки подтягиваем готовые позиции, как на странице задачи.
    refetchInterval: active ? 2000 : false,
  });
  // Склеиваем страницы и убираем возможные дубли (опрос не должен прибавлять уже показанные позиции).
  const seen = new Set<string>();
  const items = (data?.pages ?? [])
    .flatMap((p) => p.items)
    .filter((it) => (seen.has(it.item_id) ? false : (seen.add(it.item_id), true)));

  // При фильтре «незаполненные обязательные» пустые группы скрываем — иначе остаются только заголовки файлов.
  if (missingOnly && items.length === 0) return null;

  return (
    <div className="k-batch-group">
      <UnstyledButton className="k-batch-hdr" onClick={onToggle} aria-expanded={open}>
        <IconChevronDown size={16} style={{ transform: open ? "rotate(0deg)" : "rotate(-90deg)" }} />
        <Text fw={600} lineClamp={1} style={{ flex: 1, minWidth: 0 }}>
          {job.filename}
        </Text>
        <JobStatusBadge status={job.status} />
        <Text size="xs" c="var(--k-faint)" className="tabular">
          {job.total_items ? `${job.processed_items + job.failed_items} из ${job.total_items}` : "…"}
        </Text>
      </UnstyledButton>
      {open && (
        <ScrollArea type="auto">
          <Table className="k-table" highlightOnHover verticalSpacing={8} horizontalSpacing={12} miw={760}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th w={56}>Стр.</Table.Th>
                <Table.Th>Позиция</Table.Th>
                <Table.Th w={240}>Код КТРУ</Table.Th>
                <Table.Th w={90}>Кол-во</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {items.map((it) => (
                <Table.Tr key={it.item_id} className="row-clickable" onClick={() => onOpenItem(it.item_id, job.job_id)}>
                  <Table.Td>
                    <Text size="sm" c="dimmed" className="tabular">
                      {it.row_index + 1}
                    </Text>
                  </Table.Td>
                  <Table.Td style={{ overflowWrap: "anywhere" }}>
                    <Text size="sm">{it.raw_text}</Text>
                    {it.missing_required_count ? (
                      <Text size="xs" c="var(--k-lo-ink)" mt={2}>
                        не заполнено обязательных: {it.missing_required_count}
                      </Text>
                    ) : null}
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" className="mono">
                      {it.final?.ktru_code ?? "—"}
                    </Text>
                    <Text size="xs" c="dimmed" lineClamp={1}>
                      {it.final?.name}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" c="dimmed" className="tabular">
                      {it.extracted?.quantity != null ? `${it.extracted.quantity} ${it.extracted.unit ?? ""}` : "—"}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {hasNextPage && (
            <Group justify="center" p="sm">
              <Button variant="subtle" size="xs" onClick={() => fetchNextPage()} loading={isFetching}>
                Показать ещё
              </Button>
            </Group>
          )}
          {!data && <Skeleton h={80} m="sm" />}
        </ScrollArea>
      )}
    </div>
  );
}

function BatchSummary({ batch }: { batch: Batch }) {
  const s = batch.summary;
  const reviewed = (s.confirmed ?? 0) + (s.corrected ?? 0);
  const tiles: { label: string; value: number; hint?: string }[] = [
    { label: "Всего позиций", value: batch.total_items },
    { label: "Зелёные", value: s.green ?? 0 },
    { label: "Жёлтые", value: s.yellow ?? 0 },
    { label: "Красные", value: s.red ?? 0 },
    { label: "Проверено", value: reviewed },
  ];
  return (
    <Group gap={12} wrap="wrap">
      {tiles.map((t) => (
        <Card key={t.label} py="md" px="lg" style={{ flex: "1 1 150px", minWidth: 150 }}>
          <Text fz={24} fw={600} className="tabular" lh={1.2}>
            {t.value}
          </Text>
          <Text size="sm" c="dimmed">
            {t.label}
          </Text>
        </Card>
      ))}
      {(s.missing_required ?? 0) > 0 && (
        <Card py="md" px="lg" style={{ flex: "1 1 180px", minWidth: 180, borderColor: "var(--k-lo)" }}>
          <Text fz={24} fw={600} className="tabular" lh={1.2} c="var(--k-lo-ink)">
            {s.missing_required}
          </Text>
          <Text size="sm" c="var(--k-lo-ink)">
            с незаполненными обязательными
          </Text>
        </Card>
      )}
    </Group>
  );
}

function BatchExport({ batch }: { batch: Batch }) {
  const exp = useBatchExport();
  const [, setSp] = useSearchParams();
  // Выгрузка доступна, когда ни одна задача комплекта не обрабатывается (включая failed/cancelled).
  const done = batch.status !== "processing";
  const missing = batch.summary.missing_required ?? 0;

  const doExport = (format: "xlsx" | "csv" | "json" | "application") =>
    exp.mutate(
      { batch, format },
      {
        onSuccess: ({ unreviewed, missingRequired }) => {
          const warn: string[] = [];
          if (unreviewed)
            warn.push(`${unreviewed} ${plural(unreviewed, "позиция", "позиции", "позиций")} без проверки человеком`);
          if (missingRequired)
            warn.push(
              `${missingRequired} ${plural(missingRequired, "позиция", "позиции", "позиций")} с незаполненными обязательными характеристиками`,
            );
          notifications.show({
            color: warn.length ? "yellow" : "primary",
            title: "Файл выгружен",
            message: warn.length ? `${warn.join("; ")}.` : "Все позиции проверены и заполнены.",
          });
        },
        onError: (e) => notifications.show({ color: "red", message: (e as Error).message }),
      },
    );
  const run = (format: "xlsx" | "csv" | "json" | "application") => {
    if (!missing) return doExport(format);
    modals.openConfirmModal({
      title: "Незаполненные обязательные характеристики",
      children: (
        <Text size="sm">
          У {missing} {plural(missing, "позиции", "позиций", "позиций")} в комплекте не заполнены обязательные
          характеристики итогового кода. В выгрузке «для заявки» такие строки помечены «не заполнено».
        </Text>
      ),
      labels: { confirm: "Выгрузить всё равно", cancel: "Показать их" },
      confirmProps: { color: "primary" },
      onConfirm: () => doExport(format),
      onCancel: () => setSp({ missing_required: "true" }, { replace: true }),
    });
  };

  const button = (
    <Button color="#16201C" leftSection={<IconDownload size={16} />} disabled={!done} loading={exp.isPending}>
      Выгрузить комплект
    </Button>
  );
  if (!done)
    return (
      <Tooltip label="Выгрузка станет доступна, когда обработка всех файлов закончится">
        <span>{button}</span>
      </Tooltip>
    );
  const formats: { f: "xlsx" | "csv" | "json" | "application"; label: string; hint?: string }[] = [
    {
      f: "application",
      label: "Для заявки (xlsx)",
      hint: "код, наименование и характеристики — для закупочной документации",
    },
    { f: "xlsx", label: "Excel (xlsx)" },
    { f: "csv", label: "CSV" },
    { f: "json", label: "JSON" },
  ];
  return (
    <Menu position="bottom-end" withinPortal>
      <Menu.Target>{button}</Menu.Target>
      <Menu.Dropdown>
        {formats.map((x) => (
          <Menu.Item key={x.f} onClick={() => run(x.f)}>
            <Text size="sm">{x.label}</Text>
            {x.hint && (
              <Text size="xs" c="dimmed">
                {x.hint}
              </Text>
            )}
          </Menu.Item>
        ))}
      </Menu.Dropdown>
    </Menu>
  );
}
