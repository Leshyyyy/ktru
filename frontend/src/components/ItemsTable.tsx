import { ActionIcon, Box, Group, ScrollArea, Skeleton, Table, Text, Tooltip } from "@mantine/core";
import { IconCheck, IconUser } from "@tabler/icons-react";
import { useEffect } from "react";
import {
  ConfidenceBadge,
  FlagChips,
  GroupBadge,
  ItemStatusBadge,
  KtruCode,
  MissingRequiredBadge,
  useThresholds,
} from "./badges";
import { useDecisions, useWithOverlay } from "./DecisionQueue";
import type { Item } from "../api/types";

type Props = {
  items?: Item[];
  loading: boolean;
  fetching: boolean;
  selectedId: string | null;
  /** Рядом открыта карточка: без «Кол-во», уверенность и статус в одной колонке — таблица помещается целиком. */
  compact: boolean;
  onOpen: (id: string) => void;
  onGroup: (groupId: string) => void;
};

/**
 * Таблица позиций: наименование · бренд · модель, код КТРУ (ОКПД2 жирным, хвост светлее), уверенность
 * с полоской, статус, флаги, «не заполнено обязательных», клик по строке — карточка. Общая для страницы
 * задачи и групп файлов комплекта.
 */
export function ItemsTable({ items, loading, fetching, selectedId, compact, onOpen, onGroup }: Props) {
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
                <Table.Td style={{ overflowWrap: "anywhere" }}>
                  {/* Текст позиции не обрезаем: по нему проверяют код. Рядом с карточкой (там он целиком)
                      строка короче: только извлечённые наименование, бренд и модель. */}
                  {!(compact && it.extracted) && <Text size="sm">{it.raw_text}</Text>}
                  <Text size={compact ? "sm" : "xs"} c="dimmed" mt={compact ? 0 : 2} component="div">
                    {it.extracted && (
                      <>
                        <Text span inherit fw={600} c="var(--k-ink)">
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
                    <Text size="sm" c="var(--k-lo-ink)">
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
                      <Text size="xs" c="dimmed" mt={2}>
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
                      {it.missing_required_count ? (
                        <Box mt={4}>
                          <MissingRequiredBadge count={it.missing_required_count} />
                        </Box>
                      ) : null}
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
                      {it.missing_required_count ? (
                        <Box mt={4}>
                          <MissingRequiredBadge count={it.missing_required_count} />
                        </Box>
                      ) : null}
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
