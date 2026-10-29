/**
 * Карточка позиции — экраны 5 «Карточка позиции» и 6 «Ручная правка» из docs/team.
 * Панель 580 px справа; на широком экране таблица сдвигается влево и остаётся видна целиком (JobPage).
 * Клавиши: J/↓ и K/↑ — соседние строки, 1–3 — выбрать вариант, Enter — главное действие.
 *
 * Главное действие зависит от уверенности. Если у первого варианта она ниже жёлтого порога, система
 * не уверена: вариант заранее не выбран, а главная кнопка — поиск по каталогу. Так красную позицию нельзя
 * подтвердить одним Enter не глядя; выбрал вариант сам — главной становится кнопка «Выбрать вариант N».
 */
import {
  Alert,
  Box,
  Button,
  Drawer,
  Group,
  Kbd,
  Loader,
  ScrollArea,
  Skeleton,
  Stack,
  Text,
  Textarea,
  TextInput,
  Tooltip,
  UnstyledButton,
} from "@mantine/core";
import { useDebouncedValue, useHotkeys } from "@mantine/hooks";
import { IconArrowDown, IconArrowUp, IconCheck, IconMinus, IconSearch, IconX } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";
import { useCatalogItem, useCatalogSearch, useItem } from "../api/hooks";
import type { CharacteristicMatch, Item, Job, Prediction } from "../api/types";
import { ConfidenceBadge, FlagChips, GroupBadge, ItemStatusBadge, KtruCode, useThresholds } from "./badges";
import { useDecisions, useWithOverlay } from "./DecisionQueue";
import classes from "./ItemDrawer.module.css";
import { pct } from "../lib/format";

type Props = {
  itemId: string | null;
  /** Та же позиция из таблицы — для мгновенного показа, пока грузится карточка. */
  fromList?: Item;
  job: Job;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
  /** После решения — к следующей позиции «Требует проверки». */
  onAfterDecision: () => void;
  onGroup: (groupId: string) => void;
};

export function ItemDrawer({ itemId, fromList, job, onClose, onPrev, onNext, onAfterDecision, onGroup }: Props) {
  const { data, isLoading } = useItem(itemId, fromList);
  const withOverlay = useWithOverlay();
  const item = data ? withOverlay(data) : undefined;
  const { decide } = useDecisions();
  const t = useThresholds();

  // Ниже жёлтого порога (или вариантов нет) — система не уверена, первый вариант заранее не выбираем.
  const topConfidence = item?.predictions[0]?.confidence;
  const unsure = !!item && (topConfidence == null || topConfidence < t.yellow);

  // Выбранный вариант: тот, что сейчас итоговый; иначе первый, а у неуверенной позиции — никакой (0).
  const [picked, setPicked] = useState(1);
  const [manual, setManual] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const finalRank = item?.predictions.find((p) => p.ktru_code === item.final?.ktru_code)?.rank;
    const chosenByUser = item?.final?.source === "user";
    setPicked(chosenByUser ? (finalRank ?? 0) : unsure ? 0 : (finalRank ?? 1));
    setManual(item?.status === "failed");
    // Следующая позиция открывается сверху, а не на том месте, где прокрутили предыдущую.
    bodyRef.current?.scrollTo({ top: 0 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemId, data?.item_id]);

  const pending = item?.status === "pending";
  const chosen = item?.predictions.find((p) => p.rank === picked);
  const alreadyFinal = !!chosen && item?.final?.source === "user" && item.final.ktru_code === chosen.ktru_code;
  const canDecide = !!item && !!chosen && !pending && !alreadyFinal;
  // Главное действие — поиск по каталогу: вариант не выбран, а позиция неуверенная или без вариантов.
  const searchFirst = !!item && !pending && !chosen && !manual;

  const decideVariant = (p: Prediction | undefined = chosen) => {
    if (!item || !p || pending) return;
    if (p.rank === 1) decide({ kind: "confirm", item, code: p.ktru_code, name: p.name });
    else decide({ kind: "correct", item, code: p.ktru_code, name: p.name, reason: "Выбран вариант модели" });
    onAfterDecision();
  };
  const pick = (rank: number) => item && item.predictions.length >= rank && setPicked(rank);

  useHotkeys(
    itemId
      ? [
          ["j", () => onNext?.()],
          ["ArrowDown", () => onNext?.()],
          ["k", () => onPrev?.()],
          ["ArrowUp", () => onPrev?.()],
          ["1", () => pick(1)],
          ["2", () => pick(2)],
          ["3", () => pick(3)],
          ["Enter", () => (canDecide ? decideVariant() : searchFirst && setManual(true))],
        ]
      : [],
  );

  const second = item?.predictions.find((p) => p.rank === (picked === 1 ? 2 : 1));

  return (
    <Drawer
      opened={!!itemId}
      onClose={onClose}
      position="right"
      size={580}
      withOverlay={false}
      withCloseButton={false}
      trapFocus={false}
      lockScroll={false}
      closeOnClickOutside={false}
      padding={0}
      classNames={{ content: classes.content, body: classes.body }}
    >
      {!item || isLoading ? (
        <Stack p="lg">
          <Skeleton h={60} />
          <Skeleton h={160} />
          <Skeleton h={120} />
        </Stack>
      ) : (
        <>
          <div className={classes.hd}>
            <Group justify="space-between" wrap="nowrap">
              <Group gap="sm" wrap="nowrap">
                <ItemStatusBadge status={item.status} />
                <Text size="sm" c="dimmed">
                  Строка {item.row_index + 1}
                  {item.extracted?.quantity != null && ` · ${item.extracted.quantity} ${item.extracted.unit ?? ""}`}
                </Text>
              </Group>
              <Group gap={6} wrap="nowrap">
                <Button variant="default" size="compact-sm" onClick={onPrev} disabled={!onPrev} aria-label="Предыдущая">
                  <IconArrowUp size={14} />
                  <Kbd size="sm" ml={4}>
                    K
                  </Kbd>
                </Button>
                <Button variant="default" size="compact-sm" onClick={onNext} disabled={!onNext} aria-label="Следующая">
                  <IconArrowDown size={14} />
                  <Kbd size="sm" ml={4}>
                    J
                  </Kbd>
                </Button>
                <Drawer.CloseButton aria-label="Закрыть" />
              </Group>
            </Group>
            <Text fz={19} fw={600} lh={1.35} mt="sm">
              {item.raw_text}
            </Text>
            <Group gap={6}>
              <FlagChips flags={item.flags} />
              {item.group_id && (
                <Box mt={4}>
                  <GroupBadge size={item.group_size ?? 1} onClick={() => onGroup(item.group_id!)} />
                </Box>
              )}
            </Group>
          </div>

          <ScrollArea className={classes.bd} type="auto" viewportRef={bodyRef}>
            <Stack gap="lg" p="lg" pt="md">
              {pending && (
                <Alert color="gray" icon={<Loader size={16} />}>
                  Позиция ещё обрабатывается. Решение можно будет принять, когда модель предложит код.
                </Alert>
              )}
              {item.error && (
                <Alert color="red" title="Позицию не удалось обработать">
                  {item.error} Код можно указать вручную ниже.
                </Alert>
              )}
              {item.final?.source === "user" && (
                <Text size="sm">
                  Итоговый код выбран человеком: <KtruCode code={item.final.ktru_code} /> {item.final.name}
                </Text>
              )}

              {item.extracted && <Attributes item={item} />}

              {item.predictions.length > 0 && (
                <div>
                  <div className={classes.sectT}>
                    <span>Варианты КТРУ</span>
                    <span>уверенность</span>
                  </div>
                  {unsure && !pending && item.final?.source !== "user" && (
                    <Text className={classes.unsure}>
                      Система не уверена. Сравните варианты по характеристикам: выберите подходящий или найдите код в
                      каталоге.
                    </Text>
                  )}
                  <Stack gap={10}>
                    {item.predictions.map((p) => (
                      <Variant key={p.rank} p={p} picked={picked === p.rank} onPick={() => setPicked(p.rank)} />
                    ))}
                  </Stack>
                </div>
              )}

              {!manual && !searchFirst && (
                <Button
                  variant="default"
                  leftSection={<IconSearch size={16} />}
                  onClick={() => setManual(true)}
                  disabled={pending}
                  style={{ alignSelf: "flex-start" }}
                >
                  Найти другой код в каталоге
                </Button>
              )}
              {manual && (
                <ManualCode
                  item={item}
                  versionId={job.catalog_version_id}
                  disabled={pending}
                  primary={!chosen}
                  onSave={(code, name, reason) => {
                    decide({ kind: "correct", item, code, name, reason });
                    onAfterDecision();
                  }}
                  onCancel={item.status === "failed" ? undefined : () => setManual(false)}
                />
              )}
            </Stack>
          </ScrollArea>

          <div className={classes.ft}>
            {searchFirst ? (
              <>
                <Button
                  size="md"
                  flex={1}
                  leftSection={<IconSearch size={18} />}
                  onClick={() => setManual(true)}
                  rightSection={<Kbd size="sm">Enter</Kbd>}
                >
                  Найти код в каталоге
                </Button>
                {item.predictions.length > 0 && (
                  <Text size="sm" c="dimmed" maw={170} lh={1.3}>
                    или выберите вариант выше клавишами {item.predictions.map((p) => p.rank).join(", ")}
                  </Text>
                )}
              </>
            ) : manual && !chosen ? (
              <Text size="sm" c="dimmed">
                Выберите код в результатах поиска и сохраните его
                {item.predictions.length > 0 && " или вернитесь к вариантам выше"}.
              </Text>
            ) : (
              <>
                <Button
                  size="md"
                  flex={1}
                  disabled={!canDecide}
                  onClick={() => decideVariant()}
                  rightSection={canDecide ? <Kbd size="sm">Enter</Kbd> : undefined}
                >
                  {pending
                    ? "Позиция ещё обрабатывается"
                    : alreadyFinal
                      ? `Вариант ${picked} уже выбран`
                      : picked === 1
                        ? item.status === "confirmed"
                          ? "Вариант 1 подтверждён"
                          : "Подтвердить вариант 1"
                        : `Выбрать вариант ${picked}`}
                </Button>
                {second && !pending && !unsure && (
                  <Button size="md" variant="default" onClick={() => setPicked(second.rank)}>
                    Выбрать вариант {second.rank}
                  </Button>
                )}
              </>
            )}
          </div>
        </>
      )}
    </Drawer>
  );
}

// ---------------------------------------------------------------- атрибуты

function Attributes({ item }: { item: Item }) {
  const x = item.extracted!;
  const conf = Object.values(x.field_confidence ?? {}).filter((v) => v > 0);
  const avg = conf.length ? conf.reduce((a, b) => a + b, 0) / conf.length : null;
  // Вместо «правила · уверенность средняя» — вывод для человека; способ и число — во всплывающей подсказке.
  const verdict =
    avg == null
      ? null
      : avg >= 0.8
        ? "распознаны уверенно"
        : avg >= 0.6
          ? "проверьте атрибуты"
          : "распознаны неуверенно";
  const method =
    x.method === "rules" ? "по правилам" : x.method === "llm" ? "языковой моделью" : "правилами и языковой моделью";
  const chars = Object.entries(x.characteristics ?? {});
  const brandModel = [x.brand, x.model].filter(Boolean).join(" · ");
  return (
    <div>
      <div className={classes.sectT}>
        <span>Извлечённые атрибуты</span>
        {verdict && (
          <Tooltip label={`Распознаны ${method}, средняя уверенность ${pct(avg)}.`}>
            <span>{verdict}</span>
          </Tooltip>
        )}
      </div>
      <dl className={classes.kv}>
        <dt>Наименование</dt>
        <dd>{x.name}</dd>
        <dt>Бренд / модель</dt>
        <dd className={brandModel ? undefined : classes.none}>{brandModel || "не найдены"}</dd>
        {chars.length > 0 && (
          <>
            <dt>Характеристики</dt>
            <dd>{chars.map(([k, v]) => `${k}: ${v}`).join(" · ")}</dd>
          </>
        )}
      </dl>
    </div>
  );
}

// ---------------------------------------------------------------- вариант КТРУ

function Variant({ p, picked, onPick }: { p: Prediction; picked: boolean; onPick: () => void }) {
  const text = p.rank === 1 ? p.explanation : p.why_not_chosen;
  return (
    <UnstyledButton className={classes.cand} data-picked={picked || undefined} onClick={onPick} aria-pressed={picked}>
      <span className={classes.rk}>{p.rank}</span>
      <div style={{ minWidth: 0 }}>
        <KtruCode code={p.ktru_code} />
        <Text fw={500} mt={2}>
          {p.name}
        </Text>
      </div>
      <ConfidenceBadge value={p.confidence} />
      {!!p.characteristic_matches?.length && (
        <div className={classes.chars}>
          {p.characteristic_matches.map((m) => (
            <CharChip key={m.name} m={m} />
          ))}
        </div>
      )}
      {text && (
        <Text className={classes.why}>
          <b>{p.rank === 1 ? "Пояснение:" : "Почему не первый:"}</b> {text}
        </Text>
      )}
    </UnstyledButton>
  );
}

function CharChip({ m }: { m: CharacteristicMatch }) {
  const bad = m.status === "mismatch" || (m.status === "missing" && m.required);
  const kind = m.status === "match" ? "y" : bad ? "n" : "none";
  const icon = m.status === "match" ? <IconCheck size={11} /> : bad ? <IconX size={11} /> : <IconMinus size={11} />;
  return (
    <span className={classes.cm} data-kind={kind} title={m.expected ? `Допустимо по КТРУ: ${m.expected}` : undefined}>
      {icon} {m.name}: {m.found ?? "не указан"}
      {m.required && m.status !== "match" && " · обязательная"}
    </span>
  );
}

// ---------------------------------------------------------------- ручная правка

function ManualCode({
  item,
  versionId,
  disabled,
  primary,
  onSave,
  onCancel,
}: {
  item: Item;
  versionId: string;
  disabled: boolean;
  /** Сохранение кода — главное действие карточки (вариант не выбран): кнопка заливкой, поле в фокусе. */
  primary: boolean;
  onSave: (code: string, name: string, reason: string) => void;
  onCancel?: () => void;
}) {
  const [q, setQ] = useState(item.extracted?.name ?? "");
  const [debounced] = useDebouncedValue(q, 300);
  const [code, setCode] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const search = useCatalogSearch(debounced, versionId);
  const details = useCatalogItem(code, versionId);
  const found = search.data?.items ?? [];
  const picked = found.find((c) => c.ktru_code === code);

  return (
    <div>
      <div className={classes.sectT}>
        <span>Найти код в каталоге</span>
        {onCancel && (
          <UnstyledButton className={classes.link} onClick={onCancel} fz={12}>
            скрыть
          </UnstyledButton>
        )}
      </div>
      <Stack gap="sm">
        <TextInput
          id="catalog-search"
          aria-label="Поиск кода в каталоге КТРУ"
          placeholder="Начало кода или слово из названия"
          leftSection={<IconSearch size={16} />}
          rightSection={search.isFetching ? <Loader size={14} /> : undefined}
          value={q}
          onChange={(e) => setQ(e.currentTarget.value)}
          disabled={disabled}
          // Поиск открыли сами (кнопкой или Enter) — сразу можно печатать.
          autoFocus={primary}
        />
        {debounced.trim().length < 2 ? (
          <Text size="sm" c="dimmed">
            Введите хотя бы 2 символа. Ищем только в версии каталога этой задачи.
          </Text>
        ) : search.isError ? (
          <Text size="sm" c="var(--k-lo-ink)">
            {(search.error as Error).message}
          </Text>
        ) : found.length === 0 && !search.isFetching ? (
          <Text size="sm" c="dimmed">
            Ничего не нашлось. Попробуйте начало кода ОКПД2 или другое слово.
          </Text>
        ) : (
          <Stack gap={6}>
            {found.map((c) => (
              <UnstyledButton
                key={c.ktru_code}
                className={classes.result}
                data-picked={code === c.ktru_code || undefined}
                onClick={() => setCode(c.ktru_code === code ? null : c.ktru_code)}
              >
                <KtruCode code={c.ktru_code} />
                <Text size="sm">{c.name}</Text>
                {code === c.ktru_code && (
                  <Box mt={6}>
                    {details.isLoading ? (
                      <Skeleton h={18} />
                    ) : details.data?.characteristics?.length ? (
                      <Text size="xs" c="dimmed">
                        {details.data.characteristics
                          .map((ch) => `${ch.name}${ch.required ? "*" : ""}: ${ch.values.join(", ")}`)
                          .join(" · ")}
                      </Text>
                    ) : (
                      <Text size="xs" c="dimmed">
                        Характеристик у кода нет.
                      </Text>
                    )}
                  </Box>
                )}
              </UnstyledButton>
            ))}
          </Stack>
        )}
        <Textarea
          placeholder="Почему исправлено (необязательно, попадёт в журнал решений)"
          value={reason}
          onChange={(e) => setReason(e.currentTarget.value)}
          maxLength={500}
          autosize
          minRows={1}
          disabled={disabled}
        />
        <Group justify="flex-end">
          <Button
            variant={primary ? "filled" : "default"}
            disabled={!picked || disabled}
            onClick={() => picked && onSave(picked.ktru_code, picked.name, reason)}
          >
            {picked ? `Сохранить код ${picked.ktru_code}` : "Выберите код из списка"}
          </Button>
        </Group>
      </Stack>
    </div>
  );
}
