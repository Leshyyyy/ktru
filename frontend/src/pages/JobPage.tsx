import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Box,
  Button,
  Card,
  Center,
  CloseButton,
  Group,
  Menu,
  NumberInput,
  Pagination,
  Progress,
  ScrollArea,
  SegmentedControl,
  Select,
  SimpleGrid,
  Skeleton,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
  UnstyledButton,
} from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useQueryClient } from "@tanstack/react-query";
import { modals } from "@mantine/modals";
import { notifications } from "@mantine/notifications";
import {
  IconArrowLeft,
  IconCheck,
  IconChecks,
  IconDownload,
  IconInfoCircle,
  IconPlayerStop,
  IconSearch,
  IconUser,
} from "@tabler/icons-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import {
  isActive,
  useCancelJob,
  useConfirmBulk,
  useConfirmItem,
  useExport,
  useItems,
  useJob,
  type ExportFormat,
  type ItemsParams,
} from "../api/hooks";
import type { Band, Item, ItemFlag, ItemStatus, Job } from "../api/types";
import { ConfidenceBadge, FlagsIcon, ItemStatusBadge, JobStatusBadge, useThresholds } from "../components/badges";
import { ItemDrawer } from "../components/ItemDrawer";
import { BAND, dateTime, duration, FLAG, ITEM_STATUS, pct, plural } from "../lib/format";

const PAGE_SIZE = 50;

export function JobPage() {
  const { jobId = "" } = useParams();
  const { data: job, isLoading, isError, error } = useJob(jobId);

  if (isError)
    return (
      <Stack>
        <BackLink />
        <Alert color="red" title="Задача не открылась">
          {(error as Error).message}
        </Alert>
      </Stack>
    );
  if (isLoading || !job)
    return (
      <Stack>
        <Skeleton h={40} w={320} />
        <Skeleton h={90} />
        <Skeleton h={400} />
      </Stack>
    );
  return <JobView job={job} />;
}

function BackLink() {
  return (
    <Anchor component={Link} to="/jobs" size="sm" c="dimmed">
      <Group gap={4}>
        <IconArrowLeft size={14} /> Все задачи
      </Group>
    </Anchor>
  );
}

function JobView({ job }: { job: Job }) {
  const active = isActive(job.status);
  const finished = ["done", "done_with_errors", "cancelled"].includes(job.status);
  const [sp, setSp] = useSearchParams();

  // Фильтры живут в адресе страницы: ссылкой «покажи красные» можно поделиться, и они переживают обновление.
  const band = (sp.get("band") as Band | null) ?? undefined;
  const status = (sp.get("status") as ItemStatus | null) ?? undefined;
  const flag = (sp.get("flag") as ItemFlag | null) ?? undefined;
  const group_id = sp.get("group") ?? undefined;
  const sort = sp.get("sort") === "confidence_asc" ? "confidence_asc" : "row_index";
  const page = Number(sp.get("page") ?? 1);
  const [q, setQ] = useState(sp.get("q") ?? "");
  const [debouncedQ] = useDebouncedValue(q, 300);

  const setParam = (key: string, value: string | undefined | null) =>
    setSp(
      (prev) => {
        const n = new URLSearchParams(prev);
        if (value) n.set(key, value);
        else n.delete(key);
        if (key !== "page") n.delete("page");
        return n;
      },
      { replace: true },
    );

  useEffect(() => {
    if ((sp.get("q") ?? "") !== debouncedQ) setParam("q", debouncedQ || null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQ]);

  const params: ItemsParams = {
    page,
    page_size: PAGE_SIZE,
    band,
    status,
    flag,
    group_id,
    sort,
    q: debouncedQ || undefined,
  };
  const { data: list, isFetching } = useItems(job.job_id, params);
  const qc = useQueryClient();
  const progressKey = `${job.status}:${job.processed_items + job.failed_items}`;
  useEffect(() => {
    qc.invalidateQueries({ queryKey: ["items", job.job_id] });
  }, [progressKey, job.job_id, qc]);
  const [openId, setOpenId] = useState<string | null>(null);
  const idx = list?.items.findIndex((i) => i.item_id === openId) ?? -1;
  const filtered = !!(band || status || flag || group_id || debouncedQ);

  return (
    <Stack gap="lg">
      <BackLink />
      <Group justify="space-between" align="flex-start" wrap="wrap" gap="md">
        <div style={{ minWidth: 0 }}>
          <Group gap="sm" wrap="wrap">
            <Title order={2} lineClamp={2} style={{ overflowWrap: "anywhere" }}>
              {job.filename}
            </Title>
            <JobStatusBadge status={job.status} />
          </Group>
          <Text size="sm" c="dimmed" mt={4}>
            Создана {dateTime(job.created_at)}
            {job.total_items > 0 && ` · ${job.total_items} ${plural(job.total_items, "позиция", "позиции", "позиций")}`}
            {job.finished_at &&
              job.started_at &&
              ` · обработано за ${duration((+new Date(job.finished_at) - +new Date(job.created_at)) / 1000)}`}
          </Text>
        </div>
        <Group gap="sm">
          {active && <CancelButton job={job} />}
          {!active && (job.summary.green ?? 0) > 0 && <BulkConfirm job={job} />}
          <ExportMenu job={job} disabled={!finished} />
        </Group>
      </Group>

      {job.effective_mode === "degraded" && (
        <Alert color="blue" variant="light" icon={<IconInfoCircle />}>
          Языковая модель сейчас недоступна — позиции обработаны правилами и поиском. На «грязных» описаниях точность
          может быть ниже, поэтому жёлтых позиций может быть больше обычного.
        </Alert>
      )}
      {job.status === "failed" && (
        <Alert color="red" title="Обработка не удалась">
          {job.error ?? "Внутренняя ошибка."}
        </Alert>
      )}

      {active && <ProgressCard job={job} />}

      <Summary
        job={job}
        band={band}
        status={status}
        onBand={(b) => setParam("band", b)}
        onStatus={(s) => setParam("status", s)}
      />

      <Card p={0}>
        <Group p="md" gap="sm" wrap="wrap">
          <TextInput
            placeholder="Поиск по тексту позиции"
            leftSection={<IconSearch size={16} />}
            value={q}
            onChange={(e) => setQ(e.currentTarget.value)}
            rightSection={q ? <CloseButton size="sm" onClick={() => setQ("")} aria-label="Очистить" /> : null}
            w={{ base: "100%", sm: 280 }}
          />
          {/* На телефоне фильтр по цвету — плитки сводки выше, сегменты не помещаются. */}
          <SegmentedControl
            visibleFrom="sm"
            size="sm"
            value={band ?? "all"}
            onChange={(v) => setParam("band", v === "all" ? null : v)}
            data={[
              { value: "all", label: "Все" },
              { value: "green", label: "Уверенно" },
              { value: "yellow", label: "Проверить" },
              { value: "red", label: "Сомнительно" },
            ]}
          />
          <Select
            placeholder="Статус"
            size="sm"
            w={180}
            clearable
            value={status ?? null}
            onChange={(v) => setParam("status", v)}
            data={(Object.keys(ITEM_STATUS) as ItemStatus[]).map((s) => ({ value: s, label: ITEM_STATUS[s].label }))}
          />
          <Select
            placeholder="Предупреждения"
            size="sm"
            w={220}
            clearable
            value={flag ?? null}
            onChange={(v) => setParam("flag", v)}
            data={(Object.keys(FLAG) as ItemFlag[]).map((f) => ({ value: f, label: FLAG[f].label }))}
          />
          <Switch
            label="Сначала сомнительные"
            checked={sort === "confidence_asc"}
            onChange={(e) => setParam("sort", e.currentTarget.checked ? "confidence_asc" : null)}
          />
          {group_id && (
            <Badge
              size="lg"
              variant="light"
              rightSection={
                <CloseButton size="xs" onClick={() => setParam("group", null)} aria-label="Сбросить группу" />
              }
            >
              Только одинаковые товары
            </Badge>
          )}
        </Group>

        <ItemsTable
          items={list?.items}
          loading={!list}
          fetching={isFetching}
          selectedId={openId}
          onOpen={setOpenId}
          onGroup={(g) => setParam("group", g)}
        />

        {list && list.items.length === 0 && (
          <Center py={50}>
            <Stack align="center" gap={4}>
              <Text fw={500}>
                {filtered ? "Под фильтр ничего не попало" : active ? "Позиции скоро появятся" : "Позиций нет"}
              </Text>
              {filtered && (
                <Anchor
                  component="button"
                  size="sm"
                  onClick={() => {
                    setQ("");
                    setSp({}, { replace: true });
                  }}
                >
                  Сбросить фильтры
                </Anchor>
              )}
            </Stack>
          </Center>
        )}
        {list && list.total > PAGE_SIZE && (
          <Group justify="space-between" p="md">
            <Text size="sm" c="dimmed">
              Показаны {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, list.total)} из {list.total}
            </Text>
            <Pagination
              value={page}
              onChange={(p) => setParam("page", String(p))}
              total={Math.ceil(list.total / PAGE_SIZE)}
            />
          </Group>
        )}
      </Card>

      <ItemDrawer
        itemId={openId}
        fromList={idx >= 0 ? list?.items[idx] : undefined}
        job={job}
        onClose={() => setOpenId(null)}
        onPrev={idx > 0 ? () => setOpenId(list!.items[idx - 1].item_id) : undefined}
        onNext={
          list && idx >= 0 && idx < list.items.length - 1 ? () => setOpenId(list.items[idx + 1].item_id) : undefined
        }
      />
    </Stack>
  );
}

// ---------------------------------------------------------------- прогресс и сводка

function ProgressCard({ job }: { job: Job }) {
  const done = job.processed_items + job.failed_items;
  const label =
    job.status === "queued"
      ? "Задача в очереди — сейчас начнётся"
      : job.status === "parsing"
        ? "Читаем файл и считаем позиции"
        : `Обработано ${done} из ${job.total_items}`;
  return (
    <Card>
      <Stack gap="xs">
        <Group justify="space-between">
          <Text fw={600}>{label}</Text>
          <Text size="sm" c="dimmed" className="tabular">
            {job.eta_seconds != null ? `осталось ≈ ${duration(job.eta_seconds)}` : ""}
          </Text>
        </Group>
        <Progress
          value={Math.max(job.progress * 100, job.status === "processing" ? 2 : 0)}
          size="lg"
          animated
          striped
        />
        <Text size="xs" c="dimmed">
          Вкладку можно закрыть — обработка продолжится на сервере. Готовые позиции уже видны в таблице ниже, их можно
          проверять сразу.
        </Text>
      </Stack>
    </Card>
  );
}

function Summary({
  job,
  band,
  status,
  onBand,
  onStatus,
}: {
  job: Job;
  band?: Band;
  status?: ItemStatus;
  onBand: (b: Band | null) => void;
  onStatus: (s: ItemStatus | null) => void;
}) {
  const t = useThresholds();
  const s = job.summary;
  const reviewed = (s.confirmed ?? 0) + (s.corrected ?? 0);
  const totalReady = (s.green ?? 0) + (s.yellow ?? 0) + (s.red ?? 0);
  const tiles: { key: Band; value: number; sub: string }[] = [
    { key: "green", value: s.green ?? 0, sub: `от ${pct(t.green)}` },
    { key: "yellow", value: s.yellow ?? 0, sub: `${pct(t.yellow)}–${pct(t.green)}` },
    { key: "red", value: s.red ?? 0, sub: `ниже ${pct(t.yellow)}` },
  ];
  return (
    <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
      {tiles.map((x) => (
        <Tile
          key={x.key}
          active={band === x.key}
          onClick={() => onBand(band === x.key ? null : x.key)}
          color={BAND[x.key].color}
          label={BAND[x.key].label}
          value={x.value}
          sub={`${x.sub} · ${totalReady ? Math.round((x.value / totalReady) * 100) : 0}% позиций`}
        />
      ))}
      <Tile
        active={status === "confirmed" || status === "corrected"}
        onClick={() => onStatus(status === "corrected" ? null : status === "confirmed" ? "corrected" : "confirmed")}
        color="violet"
        label="Проверено человеком"
        value={reviewed}
        sub={`${s.confirmed ?? 0} подтв. · ${s.corrected ?? 0} испр.${job.failed_items ? ` · ${job.failed_items} ${plural(job.failed_items, "ошибка", "ошибки", "ошибок")}` : ""}`}
      />
    </SimpleGrid>
  );
}

function Tile(p: { active: boolean; onClick: () => void; color: string; label: string; value: number; sub: string }) {
  return (
    <UnstyledButton onClick={p.onClick} aria-pressed={p.active}>
      <Card
        padding="md"
        style={{
          borderColor: p.active ? `var(--mantine-color-${p.color}-6)` : undefined,
          boxShadow: p.active ? `0 0 0 1px var(--mantine-color-${p.color}-6)` : undefined,
        }}
      >
        <Group gap={8}>
          <Box w={8} h={8} style={{ borderRadius: 8, background: `var(--mantine-color-${p.color}-6)` }} />
          <Text size="sm" c="dimmed">
            {p.label}
          </Text>
        </Group>
        <Text fz={28} fw={650} className="tabular" lh={1.2} mt={4}>
          {p.value}
        </Text>
        <Text size="xs" c="dimmed">
          {p.sub}
        </Text>
      </Card>
    </UnstyledButton>
  );
}

// ---------------------------------------------------------------- таблица

function ItemsTable({
  items,
  loading,
  fetching,
  selectedId,
  onOpen,
  onGroup,
}: {
  items?: Item[];
  loading: boolean;
  fetching: boolean;
  selectedId: string | null;
  onOpen: (id: string) => void;
  onGroup: (groupId: string) => void;
}) {
  const confirm = useConfirmItem();
  return (
    <ScrollArea type="auto">
      <Table
        highlightOnHover
        verticalSpacing="sm"
        horizontalSpacing="md"
        miw={920}
        style={{ opacity: fetching && !loading ? 0.85 : 1, transition: "opacity .2s" }}
      >
        <Table.Thead>
          <Table.Tr>
            <Table.Th w={56}>№</Table.Th>
            <Table.Th>Позиция из файла</Table.Th>
            <Table.Th w={290}>Код КТРУ</Table.Th>
            <Table.Th w={100}>Уверенность</Table.Th>
            <Table.Th w={150}>Статус</Table.Th>
            <Table.Th w={60} />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {loading &&
            Array.from({ length: 6 }).map((_, i) => (
              <Table.Tr key={i}>
                <Table.Td colSpan={6}>
                  <Skeleton h={34} />
                </Table.Td>
              </Table.Tr>
            ))}
          {items?.map((it) => {
            const canConfirm = it.status === "processed" || it.status === "needs_review";
            return (
              <Table.Tr
                key={it.item_id}
                className={`row-clickable${selectedId === it.item_id ? " row-selected" : ""}`}
                onClick={() => onOpen(it.item_id)}
              >
                <Table.Td>
                  <Text size="sm" c="dimmed" className="tabular">
                    {it.row_index + 1}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm" lineClamp={2}>
                    {it.raw_text}
                  </Text>
                  <Group gap={6} mt={4}>
                    {it.extracted?.brand && (
                      <Badge size="xs" variant="outline" color="gray" tt="none">
                        {it.extracted.brand}
                      </Badge>
                    )}
                    {it.extracted?.model && (
                      <Badge size="xs" variant="outline" color="gray" tt="none">
                        {it.extracted.model}
                      </Badge>
                    )}
                    {it.group_id && (it.group_size ?? 1) > 1 && (
                      <Tooltip label="Показать все позиции с тем же товаром">
                        <Badge
                          size="xs"
                          variant="light"
                          style={{ cursor: "pointer" }}
                          onClick={(e) => {
                            e.stopPropagation();
                            onGroup(it.group_id!);
                          }}
                        >
                          ×{it.group_size} в файле
                        </Badge>
                      </Tooltip>
                    )}
                  </Group>
                </Table.Td>
                <Table.Td>
                  {it.status === "pending" ? (
                    <Text size="sm" c="dimmed">
                      ожидает обработки
                    </Text>
                  ) : it.status === "failed" && !it.final?.ktru_code ? (
                    <Text size="sm" c="red">
                      {it.error ?? "Ошибка обработки"}
                    </Text>
                  ) : (
                    <Group gap={6} wrap="nowrap" align="flex-start">
                      <div style={{ minWidth: 0 }}>
                        <Group gap={6} wrap="nowrap">
                          <Text className="mono" fw={600}>
                            {it.final?.ktru_code}
                          </Text>
                          {it.final?.source === "user" && (
                            <Tooltip label="Код выбран человеком">
                              <IconUser size={14} color="var(--mantine-color-violet-6)" />
                            </Tooltip>
                          )}
                          <FlagsIcon flags={it.flags} />
                        </Group>
                        <Text size="sm" c="dimmed" lineClamp={1}>
                          {it.final?.name}
                        </Text>
                      </div>
                    </Group>
                  )}
                </Table.Td>
                <Table.Td>
                  <ConfidenceBadge value={it.predictions[0]?.confidence} />
                </Table.Td>
                <Table.Td>
                  <ItemStatusBadge status={it.status} />
                </Table.Td>
                <Table.Td onClick={(e) => e.stopPropagation()}>
                  {canConfirm && (
                    <Tooltip label="Подтвердить предложенный код">
                      <ActionIcon
                        variant="light"
                        color="teal"
                        onClick={() =>
                          confirm.mutate(it.item_id, {
                            onError: (e) => notifications.show({ color: "red", message: (e as Error).message }),
                          })
                        }
                        loading={confirm.isPending && confirm.variables === it.item_id}
                        aria-label="Подтвердить"
                      >
                        <IconCheck size={16} />
                      </ActionIcon>
                    </Tooltip>
                  )}
                </Table.Td>
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
    </ScrollArea>
  );
}

// ---------------------------------------------------------------- действия с задачей

function CancelButton({ job }: { job: Job }) {
  const cancel = useCancelJob();
  return (
    <Button
      variant="default"
      leftSection={<IconPlayerStop size={16} />}
      loading={cancel.isPending}
      onClick={() =>
        cancel.mutate(job.job_id, {
          onSuccess: () => notifications.show({ message: "Обработка остановлена. Готовые позиции сохранены." }),
          onError: (e) => notifications.show({ color: "red", message: (e as Error).message }),
        })
      }
    >
      Остановить
    </Button>
  );
}

function BulkConfirm({ job }: { job: Job }) {
  const t = useThresholds();
  const bulk = useConfirmBulk(job.job_id);
  const open = () => {
    let min = t.green ?? 0.85;
    let skipFlagged = true;
    modals.openConfirmModal({
      title: "Подтвердить уверенные позиции",
      children: (
        <Stack gap="sm">
          <Text size="sm">
            Система подтвердит все ещё не проверенные позиции, у которых уверенность модели не ниже порога. Ваши ручные
            решения не изменятся.
          </Text>
          <NumberInput
            label="Порог уверенности, %"
            defaultValue={Math.round(min * 100)}
            min={50}
            max={100}
            onChange={(v) => (min = Number(v) / 100)}
          />
          <Switch
            defaultChecked
            label="Пропустить позиции с предупреждениями"
            description="Например, когда в тексте нет обязательной характеристики КТРУ. Их лучше посмотреть глазами."
            onChange={(e) => (skipFlagged = e.currentTarget.checked)}
          />
          <Text size="xs" c="dimmed">
            Массовые подтверждения не попадают в эталонные примеры: система не учится на собственных непроверенных
            ответах.
          </Text>
        </Stack>
      ),
      labels: { confirm: "Подтвердить", cancel: "Отмена" },
      confirmProps: { color: "teal" },
      onConfirm: () =>
        bulk.mutate(
          { min_confidence: min, skip_flagged: skipFlagged },
          {
            onSuccess: (r) =>
              notifications.show({
                color: "teal",
                message: r.confirmed_count
                  ? `Подтверждено ${r.confirmed_count} ${plural(r.confirmed_count, "позиция", "позиции", "позиций")}`
                  : "Подходящих позиций не нашлось",
              }),
            onError: (e) => notifications.show({ color: "red", message: (e as Error).message }),
          },
        ),
    });
  };
  return (
    <Button variant="light" color="teal" leftSection={<IconChecks size={16} />} onClick={open} loading={bulk.isPending}>
      Подтвердить уверенные
    </Button>
  );
}

function ExportMenu({ job, disabled }: { job: Job; disabled: boolean }) {
  const exp = useExport();
  const formats = useMemo(
    () =>
      [
        { f: "source", label: "Как исходный файл", hint: "тот же формат, для pdf — xlsx" },
        { f: "xlsx", label: "Excel (xlsx)" },
        { f: "csv", label: "CSV" },
        { f: "json", label: "JSON" },
      ] as { f: ExportFormat; label: string; hint?: string }[],
    [],
  );
  const run = (format: ExportFormat) =>
    exp.mutate(
      { job, format },
      {
        onSuccess: ({ unreviewed }) =>
          notifications.show({
            color: unreviewed ? "yellow" : "teal",
            title: "Файл выгружен",
            message: unreviewed
              ? `В выгрузке ${unreviewed} ${plural(unreviewed, "позиция", "позиции", "позиций")} без проверки человеком — они помечены в колонке «Источник».`
              : "Все позиции проверены.",
          }),
        onError: (e) => notifications.show({ color: "red", message: (e as Error).message }),
      },
    );
  const button = (
    <Button leftSection={<IconDownload size={16} />} disabled={disabled} loading={exp.isPending}>
      Выгрузить
    </Button>
  );
  // Отключённая кнопка не ловит наведение, поэтому подсказку вешаем на обёртку.
  if (disabled)
    return (
      <Tooltip label="Выгрузка станет доступна, когда обработка закончится">
        <span>{button}</span>
      </Tooltip>
    );
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
