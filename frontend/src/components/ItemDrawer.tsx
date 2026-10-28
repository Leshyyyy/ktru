import {
  Alert,
  Badge,
  Box,
  Button,
  Card,
  Divider,
  Drawer,
  Group,
  Kbd,
  Loader,
  Paper,
  Select,
  SimpleGrid,
  Skeleton,
  Stack,
  Table,
  Text,
  Textarea,
  ThemeIcon,
  Title,
  Tooltip,
} from "@mantine/core";
import { useDebouncedValue, useHotkeys } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import {
  IconAlertTriangle,
  IconArrowDown,
  IconArrowUp,
  IconCheck,
  IconCircleCheck,
  IconCircleDashed,
  IconCircleX,
  IconUser,
} from "@tabler/icons-react";
import { useState } from "react";
import { useCatalogSearch, useConfirmItem, useCorrectItem, useItem } from "../api/hooks";
import type { CharacteristicMatch, Item, Job, Prediction } from "../api/types";
import { FLAG } from "../lib/format";
import { ConfidenceBadge, ItemStatusBadge } from "./badges";

type Props = {
  itemId: string | null;
  /** Та же позиция из таблицы — для мгновенного показа, пока грузится карточка. */
  fromList?: Item;
  job: Job;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
};

export function ItemDrawer({ itemId, fromList, job, onClose, onPrev, onNext }: Props) {
  const { data: item, isLoading } = useItem(itemId, fromList);
  const confirm = useConfirmItem();

  const canConfirm = !!item && item.predictions.length > 0 && item.status !== "pending" && item.status !== "confirmed";
  const doConfirm = () =>
    item &&
    confirm.mutate(item.item_id, {
      onSuccess: () => {
        notifications.show({ color: "teal", message: `Позиция №${item.row_index + 1}: код подтверждён` });
        onNext?.();
      },
      onError: (e) => notifications.show({ color: "red", message: (e as Error).message }),
    });

  useHotkeys(
    itemId
      ? [
          ["ArrowDown", () => onNext?.()],
          ["j", () => onNext?.()],
          ["ArrowUp", () => onPrev?.()],
          ["k", () => onPrev?.()],
          ["Enter", () => canConfirm && doConfirm()],
        ]
      : [],
  );

  return (
    <Drawer
      opened={!!itemId}
      onClose={onClose}
      position="right"
      size="min(640px, 100vw)"
      title={
        item ? (
          <Group gap="sm">
            <Text fw={650} size="lg">
              Позиция №{item.row_index + 1}
            </Text>
            <ItemStatusBadge status={item.status} />
          </Group>
        ) : (
          <Text fw={650} size="lg">
            Позиция
          </Text>
        )
      }
      styles={{ body: { paddingBottom: 0 } }}
    >
      {isLoading || !item ? (
        <Stack>
          <Skeleton h={60} />
          <Skeleton h={160} />
          <Skeleton h={120} />
        </Stack>
      ) : (
        <ItemBody item={item} job={job} />
      )}

      {/* Панель действий прилипает к низу прокручиваемой карточки: кнопки всегда под рукой. */}
      <Paper
        pos="sticky"
        bottom={0}
        mx="calc(var(--mantine-spacing-md) * -1)"
        mt="lg"
        p="md"
        withBorder
        radius={0}
        style={{ zIndex: 2, borderLeft: 0, borderRight: 0, borderBottom: 0 }}
      >
        <Group justify="space-between">
          <Group gap="xs">
            <Button
              variant="default"
              size="sm"
              onClick={onPrev}
              disabled={!onPrev}
              leftSection={<IconArrowUp size={16} />}
            >
              Пред.
            </Button>
            <Button
              variant="default"
              size="sm"
              onClick={onNext}
              disabled={!onNext}
              leftSection={<IconArrowDown size={16} />}
            >
              След.
            </Button>
          </Group>
          <Group gap="sm">
            <Text size="xs" c="dimmed" visibleFrom="sm">
              <Kbd size="xs">Enter</Kbd> подтвердить · <Kbd size="xs">↑</Kbd> <Kbd size="xs">↓</Kbd> листать
            </Text>
            <Button
              color="teal"
              leftSection={<IconCheck size={16} />}
              onClick={doConfirm}
              disabled={!canConfirm}
              loading={confirm.isPending}
            >
              Подтвердить
            </Button>
          </Group>
        </Group>
      </Paper>
    </Drawer>
  );
}

function ItemBody({ item, job }: { item: Item; job: Job }) {
  const correct = useCorrectItem();
  const choose = (code: string, reason?: string) =>
    correct.mutate(
      { itemId: item.item_id, ktru_code: code, reason },
      {
        onSuccess: (it) =>
          notifications.show({
            color: "teal",
            message: it.status === "confirmed" ? "Код подтверждён" : `Код исправлен на ${code}`,
          }),
        onError: (e) => notifications.show({ color: "red", message: (e as Error).message }),
      },
    );

  return (
    <Stack gap="lg">
      <Paper withBorder p="md" bg="var(--mantine-color-default-hover)">
        <Text size="xs" c="dimmed" mb={4}>
          Текст позиции из файла
        </Text>
        <Text>{item.raw_text}</Text>
        {item.group_id && item.group_size && item.group_size > 1 && (
          <Text size="xs" c="dimmed" mt={6}>
            Такой же товар (наименование, бренд и модель) встречается в файле ещё {item.group_size - 1} раз.
          </Text>
        )}
      </Paper>

      {item.status === "pending" && (
        <Alert color="gray" icon={<Loader size={16} />}>
          Позиция ещё в очереди. Решение можно будет принять, когда модель предложит код.
        </Alert>
      )}
      {item.error && (
        <Alert color="red" icon={<IconCircleX />} title="Позицию не удалось обработать">
          {item.error} Код можно указать вручную ниже.
        </Alert>
      )}
      {item.flags?.map((f) => (
        <Alert key={f} color="orange" icon={<IconAlertTriangle />} title={FLAG[f].label} variant="light">
          {FLAG[f].hint}
        </Alert>
      ))}

      {item.final?.ktru_code && (
        <Card padding="md" style={{ borderColor: "var(--mantine-primary-color-light-color)" }}>
          <Group justify="space-between" wrap="nowrap" align="flex-start">
            <div>
              <Text size="xs" c="dimmed">
                Итоговый код {item.final.source === "user" ? "— выбран человеком" : "— предложен моделью"}
              </Text>
              <Group gap="xs" mt={4}>
                <Text className="mono" fw={650} size="lg">
                  {item.final.ktru_code}
                </Text>
                {item.final.source === "user" && (
                  <Tooltip label="Решение пользователя">
                    <ThemeIcon size="sm" variant="light" radius="xl">
                      <IconUser size={12} />
                    </ThemeIcon>
                  </Tooltip>
                )}
              </Group>
              <Text fw={500}>{item.final.name}</Text>
            </div>
            {item.final.source === "prediction" && <ConfidenceBadge value={item.final.confidence} size="lg" />}
          </Group>
        </Card>
      )}

      {item.predictions.length > 0 && (
        <Stack gap="sm">
          <Title order={5}>Варианты модели</Title>
          {item.predictions.map((p) => (
            <PredictionCard
              key={p.rank}
              p={p}
              chosen={item.final?.ktru_code === p.ktru_code}
              onChoose={p.rank > 1 ? () => choose(p.ktru_code, "Выбран вариант модели") : undefined}
              busy={correct.isPending}
            />
          ))}
        </Stack>
      )}

      {item.extracted && (
        <Stack gap="sm">
          <Group gap="xs">
            <Title order={5}>Что система поняла из текста</Title>
            <Badge variant="outline" color="gray" size="sm">
              {item.extracted.method === "rules"
                ? "правила"
                : item.extracted.method === "llm"
                  ? "LLM"
                  : "правила + LLM"}
            </Badge>
          </Group>
          <SimpleGrid cols={{ base: 2, xs: 3 }} spacing="xs">
            <Attr label="Наименование" value={item.extracted.name} conf={item.extracted.field_confidence?.name} />
            <Attr label="Бренд" value={item.extracted.brand} conf={item.extracted.field_confidence?.brand} />
            <Attr label="Модель" value={item.extracted.model} conf={item.extracted.field_confidence?.model} />
            {item.extracted.quantity != null && (
              <Attr label="Количество" value={`${item.extracted.quantity} ${item.extracted.unit ?? ""}`} />
            )}
            {Object.entries(item.extracted.characteristics ?? {}).map(([k, v]) => (
              <Attr key={k} label={k} value={v} />
            ))}
          </SimpleGrid>
        </Stack>
      )}

      <Divider />
      <ManualCode
        versionId={job.catalog_version_id}
        disabled={item.status === "pending"}
        onSave={(code, reason) => choose(code, reason)}
        saving={correct.isPending}
      />
    </Stack>
  );
}

function PredictionCard({
  p,
  chosen,
  onChoose,
  busy,
}: {
  p: Prediction;
  chosen: boolean;
  onChoose?: () => void;
  busy: boolean;
}) {
  return (
    <Card padding="md" bg={chosen ? "var(--mantine-primary-color-light)" : undefined}>
      <Group justify="space-between" wrap="nowrap" align="flex-start">
        <Group gap="sm" wrap="nowrap" align="flex-start">
          <Badge variant="default" circle size="lg">
            {p.rank}
          </Badge>
          <div>
            <Text className="mono" fw={600}>
              {p.ktru_code}
            </Text>
            <Text size="sm" fw={500}>
              {p.name}
            </Text>
            <Text size="xs" c="dimmed">
              ОКПД2 {p.okpd2_code}
            </Text>
          </div>
        </Group>
        <ConfidenceBadge value={p.confidence} />
      </Group>
      {(p.explanation || p.why_not_chosen) && (
        <Text size="sm" mt="sm" c={p.rank === 1 ? undefined : "dimmed"}>
          {p.explanation ?? p.why_not_chosen}
        </Text>
      )}
      {!!p.characteristic_matches?.length && <Matches list={p.characteristic_matches} />}
      {onChoose && !chosen && (
        <Group justify="flex-end" mt="sm">
          <Button size="xs" variant="light" onClick={onChoose} loading={busy}>
            Выбрать этот код
          </Button>
        </Group>
      )}
    </Card>
  );
}

function Matches({ list }: { list: CharacteristicMatch[] }) {
  const icon = (s: CharacteristicMatch["status"]) =>
    s === "match" ? (
      <IconCircleCheck size={16} color="var(--mantine-color-teal-6)" aria-label="совпадает" />
    ) : s === "mismatch" ? (
      <IconCircleX size={16} color="var(--mantine-color-red-6)" aria-label="не совпадает" />
    ) : (
      <IconCircleDashed size={16} color="var(--mantine-color-orange-6)" aria-label="не найдено" />
    );
  return (
    <Table mt="sm" fz="sm" verticalSpacing={4} withRowBorders={false}>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Характеристика КТРУ</Table.Th>
          <Table.Th>Допустимо</Table.Th>
          <Table.Th>В позиции</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {list.map((m) => (
          <Table.Tr key={m.name}>
            <Table.Td>
              <Group gap={6} wrap="nowrap">
                {icon(m.status)}
                <span>
                  {m.name}
                  {m.required && (
                    <Text span c="red" title="Обязательная характеристика">
                      {" "}
                      *
                    </Text>
                  )}
                </span>
              </Group>
            </Table.Td>
            <Table.Td c="dimmed">{m.expected ?? "—"}</Table.Td>
            <Table.Td>
              {m.found ?? (
                <Text span c="orange.7" size="sm">
                  не указано
                </Text>
              )}
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function Attr({ label, value, conf }: { label: string; value: string | null | undefined; conf?: number }) {
  const low = conf != null && conf > 0 && conf < 0.6;
  return (
    <Box>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Text size="sm" fw={500} c={value ? (low ? "orange.7" : undefined) : "dimmed"}>
        {value || "не найдено"}
      </Text>
    </Box>
  );
}

function ManualCode({
  versionId,
  disabled,
  onSave,
  saving,
}: {
  versionId: string;
  disabled: boolean;
  onSave: (code: string, reason: string) => void;
  saving: boolean;
}) {
  const [search, setSearch] = useState("");
  const [debounced] = useDebouncedValue(search, 250);
  const [code, setCode] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const { data, isFetching } = useCatalogSearch(debounced, versionId);
  const options = (data?.items ?? []).map((c) => ({ value: c.ktru_code, label: `${c.ktru_code} — ${c.name}` }));

  return (
    <Stack gap="sm">
      <Title order={5}>Указать код вручную</Title>
      <Select
        placeholder="Начните вводить код или наименование"
        searchable
        searchValue={search}
        onSearchChange={setSearch}
        data={options}
        value={code}
        onChange={setCode}
        filter={({ options }) => options}
        nothingFoundMessage={
          debounced.length >= 2 ? (isFetching ? "Ищем…" : "Ничего не найдено") : "Введите хотя бы 2 символа"
        }
        rightSection={isFetching ? <Loader size={14} /> : undefined}
        disabled={disabled}
        clearable
        comboboxProps={{ withinPortal: true, zIndex: 400 }}
      />
      <Textarea
        placeholder="Почему исправлено (необязательно) — попадёт в журнал решений"
        value={reason}
        onChange={(e) => setReason(e.currentTarget.value)}
        maxLength={500}
        autosize
        minRows={1}
        disabled={disabled}
      />
      <Group justify="flex-end">
        <Button
          variant="light"
          disabled={!code || disabled}
          loading={saving}
          onClick={() => {
            if (!code) return;
            onSave(code, reason);
            setCode(null);
            setSearch("");
            setReason("");
          }}
        >
          Сохранить код
        </Button>
      </Group>
    </Stack>
  );
}
