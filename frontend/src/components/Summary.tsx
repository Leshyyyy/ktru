import { SimpleGrid } from "@mantine/core";
import type { Band, ItemStatus } from "../api/types";
import { BAND, pct } from "../lib/format";
import { isReviewed, reviewedStatus } from "../lib/review";
import { useThresholds } from "./badges";
import { Tile } from "./Tile";

export type SummaryLike = {
  green?: number;
  yellow?: number;
  red?: number;
  confirmed?: number;
  corrected?: number;
  missing_required?: number;
};

type Props = {
  total_items: number;
  failed_items: number;
  summary: SummaryLike;
  band?: Band;
  status?: ItemStatus;
  /** true — активен фильтр «незаполненные обязательные». */
  missingRequiredActive?: boolean;
  /** Показать плитку «с незаполненными обязательными» (для комплекта — сбоку; для задачи — тоже). */
  showMissingRequired?: boolean;
  onBand: (b: Band | null) => void;
  onStatus: (s: ItemStatus | null) => void;
  onMissingRequired?: () => void;
};

const ZONE: Record<Band, "hi" | "mid" | "lo"> = { green: "hi", yellow: "mid", red: "lo" };

/**
 * Сводка-плитки: цветные числа и точки зон, клик включает фильтр. Общая для страницы задачи и комплекта.
 * «С незаполненными обязательными» — отдельная плитка (янтарная, как флаги), не высокая карточка.
 */
export function Summary({
  total_items,
  failed_items,
  summary,
  band,
  status,
  missingRequiredActive,
  showMissingRequired,
  onBand,
  onStatus,
  onMissingRequired,
}: Props) {
  const t = useThresholds();
  const s = summary;
  const reviewed = (s.confirmed ?? 0) + (s.corrected ?? 0);
  const tiles: { key: Band; label: string; value: number; hint: string }[] = [
    { key: "green", label: "Зелёные", value: s.green ?? 0, hint: `уверенность от ${pct(t.green)}` },
    { key: "yellow", label: "Жёлтые", value: s.yellow ?? 0, hint: `уверенность ${pct(t.yellow)}–${pct(t.green)}` },
    { key: "red", label: "Красные", value: s.red ?? 0, hint: `уверенность ниже ${pct(t.yellow)}` },
  ];
  return (
    <SimpleGrid cols={{ base: 2, sm: showMissingRequired ? 6 : 5 }} spacing={12}>
      <Tile active={false} onClick={() => (onBand(null), onStatus(null))} label="Всего позиций" value={total_items} />
      {tiles.map((x) => (
        <Tile
          key={x.key}
          active={band === x.key}
          onClick={() => onBand(band === x.key ? null : x.key)}
          color={`var(--k-${ZONE[x.key]})`}
          ink={`var(--k-${ZONE[x.key]}-ink)`}
          label={x.label}
          value={x.value}
          hint={`${BAND[x.key].hint}: ${x.hint}`}
        />
      ))}
      <Tile
        active={isReviewed(status)}
        onClick={() => onStatus(isReviewed(status) ? null : reviewedStatus(s))}
        color="var(--k-cor)"
        label="Проверено"
        value={reviewed}
        hint={`${s.confirmed ?? 0} подтверждено, ${s.corrected ?? 0} исправлено${failed_items ? `, ${failed_items} с ошибкой` : ""}`}
      />
      {showMissingRequired && (
        <Tile
          active={!!missingRequiredActive}
          onClick={() => onMissingRequired?.()}
          color="var(--k-flag)"
          ink="var(--k-flag)"
          label="С незаполненными обязательными"
          value={s.missing_required ?? 0}
          hint="Позиции, у итогового кода которых не заполнены обязательные характеристики."
        />
      )}
    </SimpleGrid>
  );
}
