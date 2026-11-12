/**
 * Страница комплекта — группы задач, созданных вместе (экраны доп. задач A).
 * Сводка, таблицы позиций и фильтры — те же компоненты, что у задачи (быт-компоненты в components/).
 * Логика и API не меняются.
 */
import {
  Alert,
  Anchor,
  Button,
  Card,
  Center,
  CloseButton,
  Group,
  Skeleton,
  Stack,
  Text,
  TextInput,
  Title,
  UnstyledButton,
} from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { IconChevronDown, IconSearch } from "@tabler/icons-react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { api, unwrap } from "../api/client";
import type { Batch, ItemFlag, ItemStatus } from "../api/types";
import { useBatch, useBatchExport } from "../api/hooks";
import { BatchStatusBadge, JobStatusBadge } from "../components/badges";
import { ExportMenu } from "../components/ExportMenu";
import { FilterChip } from "../components/FilterChip";
import { ItemDrawer } from "../components/ItemDrawer";
import { ItemsTable } from "../components/ItemsTable";
import { ProgressCard } from "../components/ProgressCard";
import { Summary } from "../components/Summary";
import { batchTitle } from "./JobsPage";
import type { Band } from "../api/types";
import { dateTime, duration, FLAG, plural } from "../lib/format";

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

/** Мини-сводка зон в заголовке группы — как JobProgress на списке задач. */
export function BatchZonesInline({ job }: { job: Batch["jobs"][number] }) {
  const s = job.summary;
  const zones = [
    { n: s.green ?? 0, color: "var(--k-hi)", word: ["зелёная", "зелёные", "зелёных"] as const },
    { n: s.yellow ?? 0, color: "var(--k-mid)", word: ["жёлтая", "жёлтые", "жёлтых"] as const },
    { n: s.red ?? 0, color: "var(--k-lo)", word: ["красная", "красные", "красных"] as const },
  ].filter((z) => z.n > 0);
  if (!zones.length) return null;
  return (
    <Text size="xs" c="var(--k-faint)" className="tabular" style={{ whiteSpace: "nowrap" }}>
      {zones.map((z, i) => (
        <span key={z.word[2]} style={{ color: z.color }}>
          {i > 0 && " · "}
          {z.n} {plural(z.n, z.word[0], z.word[1], z.word[2])}
        </span>
      ))}
    </Text>
  );
}

function BatchView({ batch }: { batch: Batch }) {
  const [sp, setSp] = useSearchParams();
  const band = (sp.get("band") as Band | null) ?? undefined;
  const status = (sp.get("status") as ItemStatus | null) ?? undefined;
  const flag = (sp.get("flag") as ItemFlag | null) ?? undefined;
  const missingOnly = sp.get("missing_required") === "true";
  const sort: "row_index" | "confidence_asc" = sp.get("sort") === "confidence_asc" ? "confidence_asc" : "row_index";
  const [q, setQ] = useState(sp.get("q") ?? "");
  const [debouncedQ] = useDebouncedValue(q, 300);
  const setParam = (key: string, value: string | null) => {
    const n = new URLSearchParams(sp);
    if (value) n.set(key, value);
    else n.delete(key);
    setSp(n, { replace: true });
  };
  // Поиск живёт в адресе: после паузы ввода обновляем ?q= в URL.
  useEffect(() => {
    if ((sp.get("q") ?? "") !== debouncedQ) setParam("q", debouncedQ || null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQ]);

  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [openItem, setOpenItem] = useState<{ itemId: string; jobId: string } | null>(null);
  const openItemJob = openItem ? batch.jobs.find((j) => j.job_id === openItem.jobId) : undefined;

  const jobs = batch.jobs;
  const active = batch.status === "processing";
  const processed = batch.processed_items + batch.failed_items;
  const filters = {
    band,
    status,
    flag,
    missing_required: missingOnly,
    sort,
    q: debouncedQ || undefined,
  };

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
          <BatchExport batch={batch} onShowMissing={() => setParam("missing_required", "true")} />
        </Group>
      </Stack>

      {active ? (
        <ProgressCard
          status="processing"
          processed={processed}
          total={batch.total_items}
          progress={batch.progress}
          etaSeconds={batch.eta_seconds}
          labelPrefix="Обрабатывается"
          hint="Файлы комплекта обрабатываются вместе. Готовые позиции уже видны ниже."
        />
      ) : null}

      <Summary
        total_items={batch.total_items}
        failed_items={batch.failed_items}
        summary={batch.summary}
        band={band}
        status={status}
        missingRequiredActive={missingOnly}
        showMissingRequired
        onBand={(b) => setParam("band", b)}
        onStatus={(s) => setParam("status", s)}
        onMissingRequired={() => setParam("missing_required", missingOnly ? null : "true")}
      />

      {/* Панель фильтров — как у задачи, применяется ко всем группам. */}
      <Card p="md">
        <Group gap="sm" wrap="wrap">
          <TextInput
            aria-label="Поиск по тексту позиции"
            placeholder="Поиск по тексту позиции"
            leftSection={<IconSearch size={16} />}
            value={q}
            onChange={(e) => setQ(e.currentTarget.value)}
            rightSection={q ? <CloseButton size="sm" onClick={() => setQ("")} aria-label="Очистить" /> : null}
            w={{ base: "100%", sm: 280 }}
          />
          <FilterChip
            active={status === "needs_review"}
            onClick={() => setParam("status", status === "needs_review" ? null : "needs_review")}
          >
            Только требующие проверки
          </FilterChip>
          {(batch.summary.missing_required ?? 0) > 0 && (
            <FilterChip active={missingOnly} onClick={() => setParam("missing_required", missingOnly ? null : "true")}>
              С незаполненными обязательными · {batch.summary.missing_required ?? 0}
            </FilterChip>
          )}
          <FilterChip
            active={sort === "confidence_asc"}
            onClick={() => setParam("sort", sort === "confidence_asc" ? null : "confidence_asc")}
          >
            Сначала неуверенные
          </FilterChip>
          <FilterChip active={!!flag} onClick={() => setParam("flag", flag ? null : "required_missing")}>
            {flag ? FLAG[flag].label : "С предупреждениями"}
          </FilterChip>
        </Group>
      </Card>

      {jobs.length === 0 ? (
        <Center py={50}>
          <Text fw={500}>
            {missingOnly || status || band || flag || debouncedQ ? "Под фильтр ничего не попало" : "Позиций пока нет"}
          </Text>
        </Center>
      ) : (
        <Card p={0}>
          {jobs.map((job) => (
            <BatchFileGroup
              key={job.job_id}
              job={job}
              filters={filters}
              open={expanded[job.job_id] ?? true}
              onToggle={() => setExpanded((e) => ({ ...e, [job.job_id]: !(e[job.job_id] ?? true) }))}
              selectedId={openItem?.jobId === job.job_id ? openItem.itemId : null}
              onOpenItem={(itemId) => setOpenItem({ itemId, jobId: job.job_id })}
              compact={!!openItem}
            />
          ))}
        </Card>
      )}

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

/**
 * Одна группа файла комплекта. Позиции запрашиваются отдельно от задачи (правило хуков — useItems нельзя в map()).
 * Рисуется общим ItemsTable, как на странице задачи.
 */
function BatchFileGroup({
  job,
  filters,
  open,
  onToggle,
  selectedId,
  onOpenItem,
  compact,
}: {
  job: Batch["jobs"][number];
  filters: {
    band?: Band;
    status?: ItemStatus;
    flag?: ItemFlag;
    missing_required?: boolean;
    sort?: "row_index" | "confidence_asc";
    q?: string;
  };
  open: boolean;
  onToggle: () => void;
  selectedId: string | null;
  onOpenItem: (itemId: string) => void;
  compact: boolean;
}) {
  // Контракт: PageSize максимум 200. «Показать ещё» ДОБАВЛЯЕТ следующую страницу (не заменяет).
  const PAGE = 200;
  const active = ["queued", "parsing", "processing"].includes(job.status);
  const f = filters;
  const { data, isFetching, fetchNextPage, hasNextPage, refetch } = useInfiniteQuery({
    queryKey: ["batch-items", job.job_id, f.band, f.status, f.flag, f.missing_required, f.sort, f.q],
    queryFn: ({ pageParam = 1 }) =>
      unwrap(
        api.GET("/jobs/{job_id}/items", {
          params: {
            path: { job_id: job.job_id },
            query: {
              page: pageParam,
              page_size: PAGE,
              band: f.band,
              status: f.status,
              flag: f.flag,
              missing_required: f.missing_required || undefined,
              sort: f.sort,
              q: f.q,
            },
          },
        }),
      ),
    initialPageParam: 1,
    getNextPageParam: (last, all) => (last.total > all.length * PAGE ? all.length + 1 : undefined),
    refetchInterval: active ? 2000 : false,
  });
  const seen = new Set<string>();
  const items = (data?.pages ?? [])
    .flatMap((p) => p.items)
    .filter((it) => (seen.has(it.item_id) ? false : (seen.add(it.item_id), true)));

  // Перезапрашиваем группу при смене статуса/счётчика задачи: когда обработка закончилась, последний
  // опрос мог прийти до записи результатов, и строка осталась бы «В очереди / ожидает обработки».
  useEffect(() => {
    void refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.status, job.processed_items, refetch]);

  // Пустую группу под фильтром скрываем.
  if (items.length === 0 && !active && (f.band || f.status || f.flag || f.missing_required || f.q)) return null;

  const zoneSummary = <BatchZonesInline job={job} />;
  return (
    <div className="k-batch-group">
      <div className="k-batch-hdr">
        <UnstyledButton onClick={onToggle} aria-expanded={open} style={{ display: "contents" }}>
          <IconChevronDown size={16} style={{ transform: open ? "rotate(0deg)" : "rotate(-90deg)" }} />
        </UnstyledButton>
        <Text fw={600} lineClamp={1} style={{ flex: 1, minWidth: 0 }}>
          {job.filename}
        </Text>
        <JobStatusBadge status={job.status} />
        <Text size="xs" c="var(--k-faint)" className="tabular">
          {job.total_items ? `${job.processed_items + job.failed_items} из ${job.total_items}` : "…"}
        </Text>
        {zoneSummary && <div>{zoneSummary}</div>}
        <Anchor component={Link} to={`/jobs/${job.job_id}`} size="sm" c="dimmed" onClick={(e) => e.stopPropagation()}>
          Открыть файл →
        </Anchor>
      </div>
      {open && (
        <div>
          <ItemsTable
            items={items}
            loading={!data}
            fetching={isFetching}
            selectedId={selectedId}
            compact={compact}
            onOpen={onOpenItem}
            onGroup={(_g) => {}}
          />
          {hasNextPage && (
            <Group justify="center" p="sm">
              <Button variant="subtle" size="xs" onClick={() => fetchNextPage()} loading={isFetching}>
                Показать ещё
              </Button>
            </Group>
          )}
        </div>
      )}
    </div>
  );
}

function BatchExport({ batch, onShowMissing }: { batch: Batch; onShowMissing: () => void }) {
  const exp = useBatchExport();
  const done = batch.status !== "processing";
  const missing = batch.summary.missing_required ?? 0;
  return (
    <ExportMenu
      onExport={async (format) => {
        if (format === "source") return undefined;
        const r = await exp.mutateAsync({ batch, format });
        return { unreviewed: r.unreviewed, missingRequired: r.missingRequired };
      }}
      missing={missing}
      disabled={!done}
      onShowMissing={onShowMissing}
      allowSource={false}
    />
  );
}
