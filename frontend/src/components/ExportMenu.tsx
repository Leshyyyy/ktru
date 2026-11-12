import { Button, Menu, Text, Tooltip } from "@mantine/core";
import { modals } from "@mantine/modals";
import { notifications } from "@mantine/notifications";
import { IconDownload } from "@tabler/icons-react";
import { useMemo } from "react";
import { plural } from "../lib/format";

type Format = "source" | "xlsx" | "csv" | "json" | "application";

type Props = {
  /** Действие выгрузки конкретным форматом; возвращает число незаполненных в выгрузке (для тоста). */
  onExport: (format: Format) => Promise<{ unreviewed: number; missingRequired: number } | undefined> | void;
  /** Число позиций с незаполненными обязательными (для предупреждения перед выгрузкой). */
  missing: number;
  disabled: boolean;
  /** Главная кнопка (заливка) вместо обычной. */
  emphasis?: boolean;
  /** «Показать их» из предупреждения: открыть таблицу с незаполненными обязательными. */
  onShowMissing: () => void;
  /** Допустимые форматы (по умолчанию — все, включая source). */
  allowSource?: boolean;
};

/** Меню выгрузки с предупреждением о незаполненных обязательных. Общее для задачи и комплекта. */
export function ExportMenu({ onExport, missing, disabled, emphasis, onShowMissing, allowSource = true }: Props) {
  const formats = useMemo(
    () =>
      [
        ...(allowSource
          ? [{ f: "source", label: "Как исходный файл", hint: "тот же формат; pdf выгрузим в xlsx" }]
          : []),
        {
          f: "application",
          label: "Для заявки (xlsx)",
          hint: "код, наименование и характеристики позиции — для закупочной документации",
        },
        { f: "xlsx", label: "Excel (xlsx)" },
        { f: "csv", label: "CSV" },
        { f: "json", label: "JSON" },
      ] as { f: Format; label: string; hint?: string }[],
    [allowSource],
  );

  const doExport = async (format: Format) => {
    try {
      const res = await onExport(format);
      const unreviewed = res?.unreviewed ?? 0;
      const missingRequired = res?.missingRequired ?? 0;
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
    } catch (e) {
      notifications.show({ color: "red", message: (e as Error).message });
    }
  };

  const run = (format: Format) => {
    if (!missing) return doExport(format);
    modals.openConfirmModal({
      title: "Незаполненные обязательные характеристики",
      children: (
        <Text size="sm">
          У {missing} {plural(missing, "позиции", "позиций", "позиций")} не заполнены обязательные характеристики
          итогового кода. В выгрузке «для заявки» такие строки помечены «не заполнено».
        </Text>
      ),
      labels: { confirm: "Выгрузить всё равно", cancel: "Показать их" },
      confirmProps: { color: "primary" },
      onConfirm: () => doExport(format),
      onCancel: onShowMissing,
    });
  };

  const button = (
    <Button
      {...(emphasis ? { color: "#16201C" } : { variant: "default" })}
      leftSection={<IconDownload size={16} />}
      disabled={disabled}
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
