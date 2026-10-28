import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Collapse,
  Group,
  Loader,
  ScrollArea,
  Select,
  Stack,
  Switch,
  Table,
  Tabs,
  Text,
  Textarea,
  ThemeIcon,
  Title,
} from "@mantine/core";
import { Dropzone } from "@mantine/dropzone";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import {
  IconAlertCircle,
  IconCheck,
  IconChevronDown,
  IconFileSpreadsheet,
  IconInfoCircle,
  IconPlayerPlay,
  IconSparkles,
  IconUpload,
  IconX,
} from "@tabler/icons-react";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import { ApiRequestError } from "../api/client";
import { useClassify, useConfig, useCreateJob, useUploadFile } from "../api/hooks";
import type { ClassifyResult, ColumnRole, FileInfo, Sheet } from "../api/types";
import { ConfidenceBadge } from "../components/badges";
import { fileSize, plural, ROLE } from "../lib/format";
import { SAMPLE_HEADERS, SAMPLE_ROWS } from "../mock/sample";

const ACCEPT = {
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
  "text/csv": [".csv"],
  "application/pdf": [".pdf"],
};

export function UploadPage() {
  return (
    <Stack gap="lg">
      <div>
        <Title order={2}>Подбор кодов КТРУ</Title>
        <Text c="dimmed" mt={4}>
          Загрузите спецификацию — система извлечёт наименование, бренд и модель каждой позиции и предложит код КТРУ с
          оценкой уверенности. Спорные позиции вы проверите и поправите вручную.
        </Text>
      </div>
      <Tabs defaultValue="file" keepMounted={false}>
        <Tabs.List mb="md">
          <Tabs.Tab value="file" leftSection={<IconFileSpreadsheet size={16} />}>
            Файл спецификации
          </Tabs.Tab>
          <Tabs.Tab value="quick" leftSection={<IconSparkles size={16} />}>
            Быстрая проверка строк
          </Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="file">
          <FileFlow />
        </Tabs.Panel>
        <Tabs.Panel value="quick">
          <QuickCheck />
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}

// ---------------------------------------------------------------- файл → сопоставление колонок → задача

function FileFlow() {
  const upload = useUploadFile();
  const [info, setInfo] = useState<FileInfo | null>(null);

  const send = (file: File) =>
    upload.mutate(file, {
      onSuccess: setInfo,
      onError: (e) => notifications.show({ color: "red", title: "Файл не принят", message: (e as Error).message }),
    });

  if (info) return <MappingStep info={info} onReset={() => setInfo(null)} />;

  return (
    <Stack>
      <Dropzone
        onDrop={(files) => files[0] && send(files[0])}
        onReject={(rej) => {
          const code = rej[0]?.errors[0]?.code;
          notifications.show({
            color: "red",
            title: "Файл не подходит",
            message:
              code === "file-too-large"
                ? "Файл больше 50 МБ. Разбейте его на части."
                : "Поддерживаются файлы xlsx, csv и pdf.",
          });
        }}
        maxSize={50 * 1024 * 1024}
        accept={ACCEPT}
        multiple={false}
        loading={upload.isPending}
        radius="lg"
        p="xl"
      >
        <Group justify="center" gap="xl" mih={180} style={{ pointerEvents: "none" }}>
          <Dropzone.Accept>
            <IconUpload size={48} stroke={1.4} color="var(--mantine-primary-color-filled)" />
          </Dropzone.Accept>
          <Dropzone.Reject>
            <IconX size={48} stroke={1.4} color="var(--mantine-color-red-6)" />
          </Dropzone.Reject>
          <Dropzone.Idle>
            <IconFileSpreadsheet size={48} stroke={1.4} color="var(--mantine-color-dimmed)" />
          </Dropzone.Idle>
          <div>
            <Text size="lg" fw={600}>
              Перетащите файл сюда или нажмите, чтобы выбрать
            </Text>
            <Text size="sm" c="dimmed" mt={6}>
              xlsx, csv или pdf до 50 МБ, до 1000 позиций за раз
            </Text>
          </div>
        </Group>
      </Dropzone>
      <Group gap="xs" justify="center">
        <Text size="sm" c="dimmed">
          Нет файла под рукой?
        </Text>
        <Anchor component="button" size="sm" onClick={() => send(sampleFile())} disabled={upload.isPending}>
          Открыть демо-спецификацию
        </Anchor>
      </Group>
    </Stack>
  );
}

/** Демо-спецификация как настоящий csv-файл: её можно отправить и в имитацию, и в настоящий бэкенд. */
function sampleFile() {
  const esc = (v: string) => (/[";\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  const csv = [SAMPLE_HEADERS, ...SAMPLE_ROWS].map((r) => r.map(esc).join(";")).join("\n");
  return new File([csv], "Демо_спецификация.csv", { type: "text/csv" });
}

type Mapping = Record<number, ColumnRole>;

function initialMapping(sheet: Sheet, info: FileInfo): Mapping {
  const hint = info.layout_hint;
  const m: Mapping = {};
  sheet.columns.forEach((c) => (m[c.index] = c.suggested_role));
  if (hint) {
    sheet.columns.forEach((c) => (m[c.index] = "ignore"));
    hint.column_mapping.forEach((c) => (m[c.index] = c.role));
  }
  return m;
}

function validate(m: Mapping): string | null {
  const roles = Object.values(m);
  const n = (r: ColumnRole) => roles.filter((x) => x === r).length;
  if (n("name") === 0) return "Выберите колонку с наименованием товара — без неё подобрать код нельзя.";
  if (n("name") > 1) return "Колонка с наименованием должна быть одна. Остальные отметьте как «Описание».";
  if (n("quantity") > 1) return "Колонка количества должна быть одна.";
  if (n("unit") > 1) return "Колонка единиц измерения должна быть одна.";
  return null;
}

function MappingStep({ info, onReset }: { info: FileInfo; onReset: () => void }) {
  const navigate = useNavigate();
  const config = useConfig().data;
  const createJob = useCreateJob();
  const [sheetName, setSheetName] = useState(info.sheets[0]?.name ?? "");
  const sheet = info.sheets.find((s) => s.name === sheetName) ?? info.sheets[0];
  const [mapping, setMapping] = useState<Mapping>(() => initialMapping(sheet, info));
  const [useFeedback, setUseFeedback] = useState(true);
  const [useLlm, setUseLlm] = useState(true);
  const [advanced, { toggle: toggleAdvanced }] = useDisclosure(false);
  const error = validate(mapping);
  const maxItems = config?.limits.max_items_per_job ?? 1000;
  const tooMany = sheet.row_count != null && sheet.row_count > maxItems;

  const switchSheet = (name: string | null) => {
    const s = info.sheets.find((x) => x.name === name);
    if (!s) return;
    setSheetName(s.name);
    setMapping(initialMapping(s, info));
  };

  const start = () =>
    createJob.mutate(
      {
        file_id: info.file_id,
        sheet: sheet.name,
        header_row: sheet.header_row,
        column_mapping: Object.entries(mapping)
          .filter(([, role]) => role !== "ignore")
          .map(([index, role]) => ({ index: Number(index), role })),
        options: { use_llm: !!config?.mode.llm_enabled && useLlm, use_feedback: useFeedback },
      },
      {
        onSuccess: (job) => navigate(`/jobs/${job.job_id}`),
        onError: (e) =>
          notifications.show({
            color: "red",
            title: "Не удалось запустить обработку",
            message: e instanceof ApiRequestError ? e.message : "Попробуйте ещё раз.",
          }),
      },
    );

  const roleOptions = (Object.keys(ROLE) as ColumnRole[]).map((r) => ({ value: r, label: ROLE[r] }));

  return (
    <Stack>
      <Card>
        <Group justify="space-between" align="flex-start" wrap="wrap" gap="md">
          <Group gap="md" wrap="nowrap">
            <ThemeIcon size={44} radius="md" variant="light">
              <IconFileSpreadsheet size={24} />
            </ThemeIcon>
            <div>
              <Text fw={600}>{info.filename}</Text>
              <Text size="sm" c="dimmed">
                {fileSize(info.size_bytes)} ·{" "}
                {sheet.row_count == null
                  ? "число позиций станет известно при обработке"
                  : `${sheet.row_count} ${plural(sheet.row_count, "строка", "строки", "строк")} с данными`}
              </Text>
            </div>
          </Group>
          <Group gap="sm">
            {info.sheets.length > 1 && (
              <Select
                label="Лист"
                data={info.sheets.map((s) => s.name)}
                value={sheet.name}
                onChange={switchSheet}
                allowDeselect={false}
                w={200}
              />
            )}
            <Button variant="default" onClick={onReset}>
              Другой файл
            </Button>
          </Group>
        </Group>
      </Card>

      {info.duplicate_of_job_id && (
        <Alert color="blue" icon={<IconInfoCircle />} title="Этот файл уже обрабатывался">
          Можно открыть готовый результат и не ждать повторной обработки.{" "}
          <Anchor component={Link} to={`/jobs/${info.duplicate_of_job_id}`}>
            Открыть результат
          </Anchor>
        </Alert>
      )}
      {info.layout_hint && (
        <Alert color="teal" icon={<IconCheck />}>
          Роли колонок подставлены из прошлой загрузки с такими же заголовками. Проверьте и запускайте.
        </Alert>
      )}
      {tooMany && (
        <Alert color="red" icon={<IconAlertCircle />} title="Слишком много позиций">
          В листе {sheet.row_count} строк, а за один раз можно обработать до {maxItems}. Разбейте файл на части.
        </Alert>
      )}

      <Card p={0}>
        <Stack gap={4} p="lg" pb="md">
          <Text fw={600}>Что в какой колонке?</Text>
          <Text size="sm" c="dimmed">
            Система угадала роли по заголовкам. Обязательна только колонка с наименованием; описание и характеристики
            помогают точнее подобрать код. Ниже — первые строки файла.
          </Text>
        </Stack>
        <ScrollArea type="auto">
          <Table striped withColumnBorders verticalSpacing={6} miw={sheet.columns.length * 170}>
            <Table.Thead>
              <Table.Tr>
                {sheet.columns.map((c) => (
                  <Table.Th
                    key={c.index}
                    miw={160}
                    bg={mapping[c.index] === "name" ? "var(--mantine-primary-color-light)" : undefined}
                  >
                    <Stack gap={6} py={4}>
                      <Select
                        size="xs"
                        data={roleOptions}
                        value={mapping[c.index] ?? "ignore"}
                        onChange={(v) => v && setMapping((m) => ({ ...m, [c.index]: v as ColumnRole }))}
                        allowDeselect={false}
                        aria-label={`Роль колонки «${c.header}»`}
                        comboboxProps={{ withinPortal: true }}
                      />
                      <Text size="xs" fw={600} c={mapping[c.index] === "ignore" ? "dimmed" : undefined} lineClamp={2}>
                        {c.header}
                      </Text>
                    </Stack>
                  </Table.Th>
                ))}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {sheet.preview.map((row, r) => (
                <Table.Tr key={r}>
                  {sheet.columns.map((c) => (
                    <Table.Td key={c.index} c={mapping[c.index] === "ignore" ? "dimmed" : undefined}>
                      <Text size="sm" lineClamp={2}>
                        {row[c.index]}
                      </Text>
                    </Table.Td>
                  ))}
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </ScrollArea>
      </Card>

      <Card>
        <Stack gap="sm">
          {error ? (
            <Alert color="orange" icon={<IconAlertCircle />} variant="light">
              {error}
            </Alert>
          ) : null}
          <Group justify="space-between" wrap="wrap" gap="md">
            <Button
              variant="subtle"
              color="gray"
              size="compact-sm"
              onClick={toggleAdvanced}
              rightSection={
                <IconChevronDown size={14} style={{ transform: advanced ? "rotate(180deg)" : undefined }} />
              }
            >
              Дополнительно
            </Button>
            <Button
              size="md"
              leftSection={<IconPlayerPlay size={18} />}
              onClick={start}
              disabled={!!error || tooMany}
              loading={createJob.isPending}
            >
              Запустить обработку
            </Button>
          </Group>
          <Collapse in={advanced}>
            <Stack gap="sm" pt="xs">
              <Switch
                checked={useFeedback}
                onChange={(e) => setUseFeedback(e.currentTarget.checked)}
                label="Использовать ранее подтверждённые ответы"
                description="Если такая же позиция уже была проверена человеком, код возьмётся из неё."
              />
              {config?.mode.llm_enabled && (
                <Switch
                  checked={useLlm}
                  onChange={(e) => setUseLlm(e.currentTarget.checked)}
                  label="Подключать языковую модель для сложных позиций"
                  description="Точнее на «грязных» описаниях, но обработка идёт дольше."
                />
              )}
            </Stack>
          </Collapse>
        </Stack>
      </Card>
    </Stack>
  );
}

// ---------------------------------------------------------------- быстрая проверка

function QuickCheck() {
  const classify = useClassify();
  const [text, setText] = useState(
    "Бумага офисная А4, 80 г/м2, 500 листов, Svetocopy\nНоутбук Lenovo IdeaPad 3 15IAU7, 16 ГБ",
  );
  const lines = useMemo(
    () =>
      text
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean),
    [text],
  );
  const tooMany = lines.length > 20;

  return (
    <Stack>
      <Card>
        <Stack gap="sm">
          <Textarea
            label="Позиции — по одной на строку"
            description="До 20 строк. Ничего не сохраняется: удобно, чтобы быстро проверить пару позиций."
            autosize
            minRows={4}
            maxRows={12}
            value={text}
            onChange={(e) => setText(e.currentTarget.value)}
            error={tooMany ? `Строк ${lines.length}, а можно до 20. Для большего списка загрузите файл.` : undefined}
          />
          <Group justify="flex-end">
            <Button
              onClick={() => classify.mutate(lines)}
              loading={classify.isPending}
              disabled={!lines.length || tooMany}
              leftSection={<IconSparkles size={16} />}
            >
              Подобрать коды
            </Button>
          </Group>
        </Stack>
      </Card>
      {classify.isError && <Alert color="red">{(classify.error as Error).message}</Alert>}
      {classify.isPending && (
        <Group justify="center" py="lg">
          <Loader size="sm" />
        </Group>
      )}
      {classify.data?.results.map((r, i) => (
        <QuickResult key={i} r={r} />
      ))}
    </Stack>
  );
}

function QuickResult({ r }: { r: ClassifyResult }) {
  const top = r.predictions[0];
  return (
    <Card padding="md">
      <Stack gap={8}>
        <Text size="sm" c="dimmed">
          {r.text}
        </Text>
        {r.error ? (
          <Text c="red" size="sm">
            {r.error}
          </Text>
        ) : top ? (
          <Group justify="space-between" wrap="nowrap" align="flex-start">
            <div>
              <Group gap="xs">
                <Text className="mono" fw={600}>
                  {top.ktru_code}
                </Text>
                <Text fw={500}>{top.name}</Text>
              </Group>
              <Group gap={6} mt={6}>
                {r.extracted?.brand && (
                  <Badge variant="outline" color="gray" tt="none">
                    Бренд: {r.extracted.brand}
                  </Badge>
                )}
                {r.extracted?.model && (
                  <Badge variant="outline" color="gray" tt="none">
                    Модель: {r.extracted.model}
                  </Badge>
                )}
              </Group>
              {r.predictions.length > 1 && (
                <Text size="xs" c="dimmed" mt={6}>
                  Альтернативы:{" "}
                  {r.predictions
                    .slice(1)
                    .map((p) => `${p.name} (${Math.round(p.confidence * 100)}%)`)
                    .join(", ")}
                </Text>
              )}
            </div>
            <ConfidenceBadge value={top.confidence} size="lg" />
          </Group>
        ) : (
          <Text size="sm" c="dimmed">
            Подходящих кодов не нашлось.
          </Text>
        )}
      </Stack>
    </Card>
  );
}
