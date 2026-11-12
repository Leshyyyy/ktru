import { Box, Group, Text, Tooltip, UnstyledButton } from "@mantine/core";

/** Плитка-счётчик в сводке; клик включает фильтр. Общая для страницы задачи и комплекта. */
export function Tile(p: {
  active: boolean;
  onClick: () => void;
  /** Цвет зоны для квадратика-метки. */
  color?: string;
  /** Цвет числа: тёмный вариант зоны, чтобы контраст был не ниже 4.5:1. */
  ink?: string;
  label: string;
  value: number;
  hint?: string;
}) {
  const body = (
    <UnstyledButton onClick={p.onClick} aria-pressed={p.active} className="k-tile" data-active={p.active || undefined}>
      <Text fz={24} fw={600} className="tabular" lh={1.2} c={p.ink ?? p.color}>
        {p.value}
      </Text>
      <Group gap={8} wrap="nowrap">
        {p.color && <Box w={9} h={9} style={{ borderRadius: 3, background: p.color, flex: "none" }} />}
        <Text size="sm" c="dimmed">
          {p.label}
        </Text>
      </Group>
    </UnstyledButton>
  );
  return p.hint ? <Tooltip label={p.hint}>{body}</Tooltip> : body;
}
