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
  Kbd,
  Menu,
  NumberInput,
  Pagination,
  Progress,
  ScrollArea,
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
import { useDebouncedValue, useHotkeys, useMediaQuery } from "@mantine/hooks";
import { useQueryClient } from "@tanstack/react-query";
import { modals } from "@mantine/modals";
import { notifications } from "@mantine/notifications";
import {
  IconCheck,
  IconChecks,
  IconDownload,
  IconInfoCircle,
  IconListCheck,
  IconPlayerStop,
  IconSearch,
  IconUser,
} from "@tabler/icons-react";
import { forwardRef, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import {
  isActive,
  useCancelJob,
  useConfirmBulk,
  useExport,
  useItems,
  useJob,
  type ExportFormat,
  type ItemsParams,
} from "../api/hooks";
import type { Band, Item, ItemFlag, ItemStatus, Job } from "../api/types";
import {
  ConfidenceBadge,
  FlagChips,
  GroupBadge,
  ItemStatusBadge,
  JobStatusBadge,
  KtruCode,
  useThresholds,
} from "../components/badges";
import { useDecisions, useWithOverlay } from "../components/DecisionQueue";
import { ItemDrawer } from "../components/ItemDrawer";
import { BAND, dateTime, duration, FLAG, pct, plural } from "../lib/format";

const PAGE_SIZE = 50;
/** Ширина карточки позиции (ItemDrawer) — на широком экране страница отступает на неё, таблица не закрыта. */
const DRAWER_W = 580;

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

/** Хлебные крошки: «Задачи / Спецификация.xlsx». */
function BackLink({ name }: { name?: string }) {
  return (
    <Text size="sm" c="var(--k-faint)">
      <Anchor component={Link} to="/jobs" size="sm" c="dimmed" underline="hover">
        Задачи
      </Anchor>
      {name && ` / ${name}`}
    </Text>
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

  // setSearchParams((prev) => …) получает параметры на момент рендера, и два быстрых клика подряд
  // затирают друг друга. Поэтому держим последнюю версию в ref до следующего рендера.
  const latest = useRef<URLSearchParams | null>(null);
  useEffect(() => {
    latest.current = null;
  }, [sp]);
  const setParam = (key: string, value: string | undefined | null) => {
    const n = new URLSearchParams(latest.current ?? sp);
    if (value) n.set(key, value);
    else n.delete(key);
    if (key !== "page") n.delete("page");
    latest.current = n;
    setSp(n, { replace: true });
  };

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
  const withOverlay = useWithOverlay();

  // Сколько позиций ждут человека — для главной кнопки «Проверить спорные».
  const disputed = useItems(job.job_id, { status: "needs_review", page: 1, page_size: 1 }).data?.total ?? 0;
  // Карточка открыта на широком экране — страница отступает на её ширину, таблица видна целиком.
  const wide = useMediaQuery("(min-width: 75em)");
  const split = !!openId && !!wide;

  /** Очередь проверки: только позиции «Требует проверки», карточка первой из них. */
  const startReview = () => {
    setQ("");
    const n = new URLSearchParams();
    if (sort !== "row_index") n.set("sort", sort);
    n.set("status", "needs_review");
    n.set("open", "first");
    latest.current = n;
    setSp(n, { replace: true });
  };
  // ?open=first — открыть первую позицию, как только загрузится список (кнопка выше и ссылка из списка задач).
  const openFirst = sp.get("open") === "first";
  useEffect(() => {
    if (!openFirst || !list) return;
    if (list.items[0]) setOpenId(list.items[0].item_id);
    setParam("open", null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openFirst, list]);

  /** Следующая позиция «Требует проверки» ниже текущей на странице; если таких нет — просто следующая. */
  const nextDisputed = (): string | null => {
    const rest = (list?.items ?? []).slice(idx + 1).map(withOverlay);
    return (rest.find((i) => i.status === "needs_review") ?? rest[0])?.item_id ?? null;
  };

  // Карточка закрыта — J/K и Enter открывают строки таблицы.
  useHotkeys(
    openId
      ? []
      : [
          ["j", () => list?.items[0] && setOpenId(list.items[0].item_id)],
          ["Enter", () => list?.items[0] && setOpenId(list.items[0].item_id)],
        ],
  );

  return (
    <Stack
      gap="lg"
      // Отступ справа = ширина карточки минус то, что уже даёт поле страницы (40 px и поля по краям после 1440).
      style={split ? { marginRight: `calc(${DRAWER_W - 24}px - max(0px, (100vw - 1440px) / 2))` } : undefined}
    >
      <Stack gap={6}>
        <BackLink name={job.filename} />
        <Group justify="space-between" align="flex-start" wrap="wrap" gap="md">
          <div style={{ minWidth: 0 }}>
            <Group gap="sm" wrap="wrap">
              <Title order={2} lineClamp={2} style={{ overflowWrap: "anywhere" }}>
                {job.filename}
              </Title>
              <JobStatusBadge status={job.status} />
              {job.total_items > 0 && (
                <span className="k-pill">
                  {job.total_items} {plural(job.total_items, "позиция", "позиции", "позиций")} · проверено{" "}
                  {(job.summary.confirmed ?? 0) + (job.summary.corrected ?? 0)}
                </span>
              )}
            </Group>
            <Text size="sm" c="dimmed" mt={4}>
              Создана {dateTime(job.created_at)}
              {job.finished_at &&
                job.started_at &&
                ` · обработано за ${duration((+new Date(job.finished_at) - +new Date(job.created_at)) / 1000)}`}
            </Text>
          </div>
          <Group gap="sm">
            {active && <CancelButton job={job} />}
            {disputed > 0 && (
              <Button leftSection={<IconListCheck size={16} />} onClick={startReview}>
                Проверить спорные · {disputed}
              </Button>
            )}
            {!active && (job.summary.green ?? 0) > 0 && <BulkConfirm job={job} />}
            {/* Пока есть спорные, главное — проверка; когда их нет, главной становится выгрузка. */}
            <ExportMenu job={job} disabled={!finished} emphasis={disputed === 0} />
          </Group>
        </Group>
      </Stack>

      {job.effective_mode === "degraded" && (
        <Tooltip label="Языковая модель недоступна: позиции обработаны правилами и поиском. На «грязных» описаниях жёлтых позиций может быть больше.">
          <span className="k-mode">
            <IconInfoCircle size={14} /> Без LLM: работают правила и реранкер
          </span>
        </Tooltip>
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
          {/* Включается кнопкой «Проверить спорные»: видно, почему в таблице не все строки, и можно снять. */}
          <FilterChip
            active={status === "needs_review"}
            onClick={() => setParam("status", status === "needs_review" ? null : "needs_review")}
          >
            Только требующие проверки
          </FilterChip>
          <FilterChip
            active={sort === "confidence_asc"}
            onClick={() => setParam("sort", sort === "confidence_asc" ? null : "confidence_asc")}
          >
            Сначала неуверенные
          </FilterChip>
          <Menu position="bottom-start" withinPortal>
            <Menu.Target>
              <FilterChip active={!!flag}>{flag ? FLAG[flag].label : "С предупреждениями"}</FilterChip>
            </Menu.Target>
            <Menu.Dropdown>
              {(Object.keys(FLAG) as ItemFlag[]).map((f) => (
                <Menu.Item key={f} onClick={() => setParam("flag", f)}>
                  {FLAG[f].label}
                </Menu.Item>
              ))}
              {flag && (
                <Menu.Item c="dimmed" onClick={() => setParam("flag", null)}>
                  Показать все
                </Menu.Item>
              )}
            </Menu.Dropdown>
          </Menu>
          {job.failed_items > 0 && (
            <FilterChip
              active={status === "failed"}
              onClick={() => setParam("status", status === "failed" ? null : "failed")}
            >
              Ошибки · {job.failed_items}
            </FilterChip>
          )}
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
          {!split && (
            <Text size="xs" c="var(--k-faint)" ml="auto" visibleFrom="md">
              <Kbd size="xs">J</Kbd> <Kbd size="xs">K</Kbd> — по строкам, <Kbd size="xs">Enter</Kbd> — открыть и
              подтвердить
            </Text>
          )}
        </Group>

        <ItemsTable
          items={list?.items}
          loading={!list}
          fetching={isFetching}
          selectedId={openId}
          compact={split}
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
        onAfterDecision={() => setOpenId(nextDisputed())}
        onGroup={(g) => setParam("group", g)}
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
  const tiles: { key: Band; label: string; value: number; hint: string }[] = [
    { key: "green", label: "Зелёные", value: s.green ?? 0, hint: `уверенность от ${pct(t.green)}` },
    { key: "yellow", label: "Жёлтые", value: s.yellow ?? 0, hint: `уверенность ${pct(t.yellow)}–${pct(t.green)}` },
    { key: "red", label: "Красные", value: s.red ?? 0, hint: `уверенность ниже ${pct(t.yellow)}` },
  ];
  return (
    <SimpleGrid cols={{ base: 2, sm: 5 }} spacing={12}>
      <Tile
        active={false}
        onClick={() => (onBand(null), onStatus(null))}
        label="Всего позиций"
        value={job.total_items}
      />
      {tiles.map((x) => (
        <Tile
          key={x.key}
          active={band === x.key}
          onClick={() => onBand(band === x.key ? null : x.key)}
          color={`var(--k-${x.key === "green" ? "hi" : x.key === "yellow" ? "mid" : "lo"})`}
          label={x.label}
          value={x.value}
          hint={`${BAND[x.key].hint}: ${x.hint}`}
        />
      ))}
      <Tile
        active={status === "confirmed" || status === "corrected"}
        onClick={() => onStatus(status === "corrected" ? null : status === "confirmed" ? "corrected" : "confirmed")}
        color="var(--k-cor)"
        label="Проверено"
        value={reviewed}
        hint={`${s.confirmed ?? 0} подтверждено, ${s.corrected ?? 0} исправлено${job.failed_items ? `, ${job.failed_items} с ошибкой` : ""}`}
      />
    </SimpleGrid>
  );
}

function Tile(p: {
  active: boolean;
  onClick: () => void;
  color?: string;
  label: string;
  value: number;
  hint?: string;
}) {
  const body = (
    <UnstyledButton onClick={p.onClick} aria-pressed={p.active} className="k-tile" data-active={p.active || undefined}>
      <Text fz={24} fw={700} className="tabular" lh={1.2} c={p.color}>
        {p.value}
      </Text>
      <Group gap={7} wrap="nowrap">
        {p.color && <Box w={9} h={9} style={{ borderRadius: 3, background: p.color, flex: "none" }} />}
        <Text size="sm" c="dimmed">
          {p.label}
        </Text>
      </Group>
    </UnstyledButton>
  );
  return p.hint ? <Tooltip label={p.hint}>{body}</Tooltip> : body;
}

/** Фильтр-чип из макета: выбранный подсвечен лаймовым. */
const FilterChip = forwardRef<HTMLButtonElement, { active: boolean; onClick?: () => void; children: ReactNode }>(
  function FilterChip({ active, onClick, children, ...rest }, ref) {
    return (
      // Menu.Target передаёт свои className и обработчики — объединяем, а не затираем.
      <UnstyledButton
        ref={ref}
        {...rest}
        className={["k-chip", (rest as { className?: string }).className].filter(Boolean).join(" ")}
        data-active={active || undefined}
        onClick={onClick ?? (rest as { onClick?: () => void }).onClick}
      >
        {children}
      </UnstyledButton>
    );
  },
);

// ---------------------------------------------------------------- таблица

function ItemsTable({
  items,
  loading,
  fetching,
  selectedId,
  compact,
  onOpen,
  onGroup,
}: {
  items?: Item[];
  loading: boolean;
  fetching: boolean;
  selectedId: string | null;
  /** Рядом открыта карточка: без «Кол-во», уверенность и статус в одной колонке — таблица помещается целиком. */
  compact: boolean;
  onOpen: (id: string) => void;
  onGroup: (groupId: string) => void;
}) {
  const { decide } = useDecisions();
  const withOverlay = useWithOverlay();
  const t = useThresholds();
  const cols = compact ? 4 : 7;
  // Карточка открыта (J/K, переход к следующей спорной) — выбранная строка всегда в поле зрения.
  useEffect(() => {
    if (!selectedId) return;
    document.querySelector(`[data-item-id="${selectedId}"]`)?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);
  return (
    <ScrollArea type="auto">
      <Table
        className="k-table"
        highlightOnHover
        verticalSpacing={10}
        horizontalSpacing={12}
        miw={compact ? 560 : 1040}
        style={{ opacity: fetching && !loading ? 0.85 : 1, transition: "opacity .2s" }}
      >
        <Table.Thead>
          <Table.Tr>
            <Table.Th w={compact ? 52 : 64}>Стр.</Table.Th>
            <Table.Th>{compact ? "Позиция" : "Позиция и извлечённые атрибуты"}</Table.Th>
            {!compact && <Table.Th w={90}>Кол-во</Table.Th>}
            <Table.Th w={compact ? 200 : 300}>Код КТРУ</Table.Th>
            {compact ? (
              <Table.Th w={140}>Уверенность</Table.Th>
            ) : (
              <>
                <Table.Th w={130}>Уверенность</Table.Th>
                <Table.Th w={190}>Статус</Table.Th>
              </>
            )}
            {/* Рядом открыта карточка — решения принимаются в ней, быстрая кнопка в строке не нужна. */}
            {!compact && <Table.Th w={52} />}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {loading &&
            Array.from({ length: 6 }).map((_, i) => (
              <Table.Tr key={i}>
                <Table.Td colSpan={cols}>
                  <Skeleton h={34} />
                </Table.Td>
              </Table.Tr>
            ))}
          {items?.map(withOverlay).map((it) => {
            // Быстрое подтверждение из таблицы — только когда система уверена хотя бы на жёлтый порог;
            // красную позицию подтверждают из карточки, сравнив варианты.
            const unsure = (it.predictions[0]?.confidence ?? 0) < t.yellow;
            const canConfirm = (it.status === "processed" || it.status === "needs_review") && !unsure;
            return (
              <Table.Tr
                key={it.item_id}
                data-item-id={it.item_id}
                className={`row-clickable${selectedId === it.item_id ? " row-selected" : ""}`}
                onClick={() => onOpen(it.item_id)}
              >
                <Table.Td>
                  <Text size="sm" c="dimmed" className="tabular">
                    {it.row_index + 1}
                  </Text>
                </Table.Td>
                <Table.Td style={{ minWidth: 0 }}>
                  <Text size="sm" lineClamp={1}>
                    {it.raw_text}
                  </Text>
                  <Text size="xs" c="dimmed" mt={2} component="div">
                    {it.extracted && (
                      <>
                        <Text span size="xs" fw={600} c="var(--k-ink)">
                          {it.extracted.name}
                        </Text>
                        {[it.extracted.brand, it.extracted.model].filter(Boolean).map((v) => ` · ${v}`)}
                      </>
                    )}
                    {it.group_id && (
                      <Box component="span" ml={6}>
                        <GroupBadge size={it.group_size ?? 1} onClick={() => onGroup(it.group_id!)} />
                      </Box>
                    )}
                  </Text>
                </Table.Td>
                {!compact && (
                  <Table.Td>
                    <Text size="sm" c="dimmed" className="tabular" style={{ whiteSpace: "nowrap" }}>
                      {it.extracted?.quantity != null ? `${it.extracted.quantity} ${it.extracted.unit ?? ""}` : "—"}
                    </Text>
                  </Table.Td>
                )}
                <Table.Td>
                  {it.status === "pending" ? (
                    <Text size="sm" c="dimmed">
                      ожидает обработки
                    </Text>
                  ) : it.status === "failed" && !it.final?.ktru_code ? (
                    <Text size="sm" c="var(--k-lo)">
                      {it.error ?? "Ошибка обработки"}
                    </Text>
                  ) : (
                    <div style={{ minWidth: 0 }}>
                      <Group gap={6} wrap="nowrap">
                        <KtruCode code={it.final?.ktru_code} />
                        {it.final?.source === "user" && (
                          <Tooltip label="Код выбран человеком">
                            <IconUser size={14} color="var(--k-cor)" />
                          </Tooltip>
                        )}
                      </Group>
                      <Text size="xs" c="dimmed" lineClamp={1} mt={2}>
                        {it.final?.name}
                      </Text>
                    </div>
                  )}
                </Table.Td>
                {compact ? (
                  <Table.Td>
                    <ConfidenceBadge value={it.predictions[0]?.confidence} />
                    <Box mt={6}>
                      <ItemStatusBadge status={it.status} />
                    </Box>
                  </Table.Td>
                ) : (
                  <>
                    <Table.Td>
                      <ConfidenceBadge value={it.predictions[0]?.confidence} />
                    </Table.Td>
                    <Table.Td>
                      <ItemStatusBadge status={it.status} />
                      <FlagChips flags={it.flags} />
                    </Table.Td>
                  </>
                )}
                {!compact && (
                  <Table.Td onClick={(e) => e.stopPropagation()}>
                    {canConfirm && it.predictions[0] && (
                      <Tooltip label="Подтвердить предложенный код">
                        <ActionIcon
                          variant="light"
                          onClick={() =>
                            decide({
                              kind: "confirm",
                              item: it,
                              code: it.predictions[0].ktru_code,
                              name: it.predictions[0].name,
                            })
                          }
                          aria-label="Подтвердить"
                        >
                          <IconCheck size={16} />
                        </ActionIcon>
                      </Tooltip>
                    )}
                  </Table.Td>
                )}
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
            onChange={(v) => {
              // Пустое поле даёт "", а Number("") === 0 — это подтвердило бы всё подряд. Берём только число 50–100.
              const n = typeof v === "number" ? v : Number.NaN;
              min = Number.isFinite(n) && n >= 50 && n <= 100 ? n / 100 : Number.NaN;
            }}
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
      confirmProps: { color: "primary" },
      onConfirm: () => {
        if (!Number.isFinite(min)) {
          notifications.show({
            color: "red",
            message: "Порог должен быть числом от 50 до 100%. Ничего не подтверждено.",
          });
          return;
        }
        bulk.mutate(
          { min_confidence: min, skip_flagged: skipFlagged },
          {
            onSuccess: (r) =>
              notifications.show({
                color: "primary",
                message: r.confirmed_count
                  ? `Подтверждено ${r.confirmed_count} ${plural(r.confirmed_count, "позиция", "позиции", "позиций")}`
                  : "Подходящих позиций не нашлось",
              }),
            onError: (e) => notifications.show({ color: "red", message: (e as Error).message }),
          },
        );
      },
    });
  };
  return (
    <Button variant="default" leftSection={<IconChecks size={16} />} onClick={open} loading={bulk.isPending}>
      Подтвердить все от {pct(t.green)}
    </Button>
  );
}

function ExportMenu({ job, disabled, emphasis }: { job: Job; disabled: boolean; emphasis: boolean }) {
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
            color: unreviewed ? "yellow" : "primary",
            title: "Файл выгружен",
            message: unreviewed
              ? `В выгрузке ${unreviewed} ${plural(unreviewed, "позиция", "позиции", "позиций")} без проверки человеком — они помечены в колонке «Источник».`
              : "Все позиции проверены.",
          }),
        onError: (e) => notifications.show({ color: "red", message: (e as Error).message }),
      },
    );
  const button = (
    <Button
      {...(emphasis ? { color: "#16201C" } : { variant: "default" })}
      leftSection={<IconDownload size={16} />}
      disabled={disabled}
      loading={exp.isPending}
    >
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
