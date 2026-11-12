import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Center,
  CloseButton,
  Group,
  Kbd,
  Menu,
  NumberInput,
  Pagination,
  Skeleton,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { useDebouncedValue, useHotkeys, useMediaQuery } from "@mantine/hooks";
import { useQueryClient } from "@tanstack/react-query";
import { modals } from "@mantine/modals";
import { notifications } from "@mantine/notifications";
import { IconChecks, IconInfoCircle, IconListCheck, IconPlayerStop, IconSearch } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import {
  isActive,
  useBatch,
  useCancelJob,
  useConfirmBulk,
  useExport,
  useItems,
  useJob,
  type ExportFormat,
  type ItemsParams,
} from "../api/hooks";
import type { Band, Batch, ItemFlag, ItemStatus, Job } from "../api/types";
import { JobStatusBadge, useThresholds } from "../components/badges";
import { useWithOverlay } from "../components/DecisionQueue";
import { ExportMenu } from "../components/ExportMenu";
import { FilterChip } from "../components/FilterChip";
import { ItemDrawer } from "../components/ItemDrawer";
import { ItemsTable } from "../components/ItemsTable";
import { ProgressCard } from "../components/ProgressCard";
import { Summary } from "../components/Summary";
import { dateTime, duration, FLAG, pct, pctInt, plural } from "../lib/format";
import { isReviewed } from "../lib/review";

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

/** Хлебные крошки: «Задачи / Комплект «…» / Спецификация.xlsx». */
function BackLink({ name, batch }: { name?: string; batch?: Pick<Batch, "batch_id" | "name"> | null }) {
  return (
    <Text size="sm" c="var(--k-faint)" style={{ overflowWrap: "anywhere" }}>
      <Anchor component={Link} to="/jobs" size="sm" c="dimmed" underline="hover">
        Задачи
      </Anchor>
      {batch && (
        <>
          {" / "}
          <Anchor component={Link} to={`/batches/${batch.batch_id}`} size="sm" c="dimmed" underline="hover">
            Комплект{" "}
            {batch.name ? (
              <Text component="span" inherit>
                «{batch.name}»
              </Text>
            ) : null}
          </Anchor>
        </>
      )}
      {name && ` / ${name}`}
    </Text>
  );
}

function JobView({ job }: { job: Job }) {
  const active = isActive(job.status);
  const finished = ["done", "done_with_errors", "cancelled"].includes(job.status);
  const [sp, setSp] = useSearchParams();
  // Хлебные крошки с названием комплекта, если задача из комплекта (пустой id — запрос не используем).
  const batchQuery = useBatch(job.batch_id ?? "");
  // Выгрузка — как раньше внутри ExportMenu, теперь колбэком в общий компонент.
  const expMutation = useExport();
  const exp = async (format: ExportFormat) => {
    const r = await expMutation.mutateAsync({ job, format });
    return { unreviewed: r.unreviewed, missingRequired: r.missingRequired };
  };

  // Фильтры живут в адресе страницы: ссылкой «покажи красные» можно поделиться, и они переживают обновление.
  const band = (sp.get("band") as Band | null) ?? undefined;
  const status = (sp.get("status") as ItemStatus | null) ?? undefined;
  const flag = (sp.get("flag") as ItemFlag | null) ?? undefined;
  const group_id = sp.get("group") ?? undefined;
  const missingRequired = sp.get("missing_required") === "true";
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
    missing_required: missingRequired || undefined,
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
  const filtered = !!(band || status || flag || group_id || missingRequired || debouncedQ);
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
        <BackLink name={job.filename} batch={job.batch_id && batchQuery.data ? batchQuery.data : null} />
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
            <ExportMenu
              onExport={(format) => exp(format)}
              missing={job.summary.missing_required ?? 0}
              disabled={!finished}
              emphasis={disputed === 0}
              onShowMissing={() => setParam("missing_required", "true")}
            />
          </Group>
        </Group>
      </Stack>

      {job.effective_mode === "degraded" && (
        <Tooltip label="Языковая модель недоступна: позиции разобраны правилами и поиском по каталогу. На «грязных» описаниях жёлтых позиций может быть больше.">
          <span className="k-mode">
            <IconInfoCircle size={14} /> Без языковой модели: коды подобраны поиском по каталогу
          </span>
        </Tooltip>
      )}
      {job.status === "failed" && (
        <Alert color="red" title="Обработка не удалась">
          {job.error ?? "Внутренняя ошибка."}
        </Alert>
      )}

      {active && (
        <ProgressCard
          status={job.status as "queued" | "parsing" | "processing"}
          processed={job.processed_items + job.failed_items}
          total={job.total_items}
          progress={job.progress}
          etaSeconds={job.eta_seconds}
          hint="Вкладку можно закрыть: обработка продолжится на сервере. Готовые позиции уже видны в таблице ниже, их можно проверять сразу."
        />
      )}

      <Summary
        total_items={job.total_items}
        failed_items={job.failed_items}
        summary={job.summary}
        band={band}
        status={status}
        missingRequiredActive={missingRequired}
        showMissingRequired
        onBand={(b) => setParam("band", b)}
        onStatus={(s) => setParam("status", s)}
        onMissingRequired={() => setParam("missing_required", missingRequired ? null : "true")}
      />

      <Card p={0}>
        <Group p="md" gap="sm" wrap="wrap">
          <TextInput
            id="items-search"
            aria-label="Поиск по тексту позиции"
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
          {(job.summary.missing_required ?? 0) > 0 && (
            <FilterChip
              active={missingRequired}
              onClick={() => setParam("missing_required", missingRequired ? null : "true")}
            >
              С незаполненными обязательными · {job.summary.missing_required}
            </FilterChip>
          )}
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
          {/* Открыто плиткой «Проверено»: API фильтрует по одному статусу, поэтому переключатель между двумя. */}
          {isReviewed(status) &&
            (["confirmed", "corrected"] as const).map((s) => (
              <FilterChip key={s} active={status === s} onClick={() => setParam("status", status === s ? null : s)}>
                {s === "confirmed" ? "Подтверждённые" : "Исправленные"} · {job.summary[s] ?? 0}
              </FilterChip>
            ))}
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
              <Kbd size="sm">J</Kbd> <Kbd size="sm">K</Kbd> переход по строкам, <Kbd size="sm">Enter</Kbd> открыть и
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
              Показаны с {(page - 1) * PAGE_SIZE + 1} по {Math.min(page * PAGE_SIZE, list.total)} из {list.total}
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
            defaultValue={pctInt(min)}
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
