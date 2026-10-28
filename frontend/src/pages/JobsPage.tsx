import {
  ActionIcon,
  Alert,
  Button,
  Card,
  Center,
  Group,
  Menu,
  Pagination,
  Progress,
  ScrollArea,
  Skeleton,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { modals } from "@mantine/modals";
import { notifications } from "@mantine/notifications";
import { IconDots, IconFileUpload, IconPlayerStop, IconTrash } from "@tabler/icons-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { isActive, useCancelJob, useDeleteJob, useJobs } from "../api/hooks";
import type { Job } from "../api/types";
import { JobStatusBadge } from "../components/badges";
import { dateTime, plural } from "../lib/format";

export function JobsPage() {
  const [page, setPage] = useState(1);
  const { data, isLoading, isError, error } = useJobs(page);
  const navigate = useNavigate();

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <div>
          <Title order={2}>Задачи</Title>
          <Text c="dimmed" mt={4}>
            Обработка идёт на сервере: вкладку можно закрыть и вернуться к результату позже.
          </Text>
        </div>
        <Button component={Link} to="/" leftSection={<IconFileUpload size={16} />}>
          Загрузить файл
        </Button>
      </Group>

      {isError && <Alert color="red">{(error as Error).message}</Alert>}

      <Card p={0}>
        <ScrollArea type="auto">
          <Table highlightOnHover verticalSpacing="sm" horizontalSpacing="lg" miw={760}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Файл</Table.Th>
                <Table.Th w={170}>Статус</Table.Th>
                <Table.Th w={220}>Прогресс</Table.Th>
                <Table.Th w={150}>Создана</Table.Th>
                <Table.Th w={50} />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {isLoading &&
                Array.from({ length: 3 }).map((_, i) => (
                  <Table.Tr key={i}>
                    <Table.Td colSpan={5}>
                      <Skeleton h={22} />
                    </Table.Td>
                  </Table.Tr>
                ))}
              {data?.items.map((job) => (
                <Table.Tr key={job.job_id} className="row-clickable" onClick={() => navigate(`/jobs/${job.job_id}`)}>
                  <Table.Td>
                    <Text fw={500} lineClamp={1}>
                      {job.filename}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {job.total_items
                        ? `${job.total_items} ${plural(job.total_items, "позиция", "позиции", "позиций")}`
                        : "позиции считаются"}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <JobStatusBadge status={job.status} />
                  </Table.Td>
                  <Table.Td>
                    <JobProgress job={job} />
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" className="tabular">
                      {dateTime(job.created_at)}
                    </Text>
                  </Table.Td>
                  <Table.Td onClick={(e) => e.stopPropagation()}>
                    <JobMenu job={job} />
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </ScrollArea>
        {data && data.items.length === 0 && (
          <Center py={60}>
            <Stack align="center" gap="xs">
              <Text fw={500}>Задач пока нет</Text>
              <Text size="sm" c="dimmed">
                Загрузите первую спецификацию — она появится здесь.
              </Text>
              <Button component={Link} to="/" variant="light" mt="xs">
                Загрузить файл
              </Button>
            </Stack>
          </Center>
        )}
      </Card>
      {data && data.total > data.page_size && (
        <Group justify="center">
          <Pagination value={page} onChange={setPage} total={Math.ceil(data.total / data.page_size)} />
        </Group>
      )}
    </Stack>
  );
}

function JobProgress({ job }: { job: Job }) {
  const done = job.processed_items + job.failed_items;
  return (
    <Stack gap={4}>
      <Progress
        value={job.progress * 100}
        size="sm"
        animated={isActive(job.status)}
        color={job.status === "cancelled" ? "gray" : job.failed_items ? "orange" : undefined}
      />
      <Text size="xs" c="dimmed" className="tabular">
        {job.total_items ? `${done} из ${job.total_items}` : "—"}
        {(job.summary.green ?? 0) + (job.summary.yellow ?? 0) + (job.summary.red ?? 0) > 0 &&
          ` · ${job.summary.green ?? 0} зел. / ${job.summary.yellow ?? 0} жёлт. / ${job.summary.red ?? 0} красн.`}
      </Text>
    </Stack>
  );
}

function JobMenu({ job }: { job: Job }) {
  const cancel = useCancelJob();
  const del = useDeleteJob();
  const onError = (e: Error) => notifications.show({ color: "red", message: e.message });

  const confirmDelete = () =>
    modals.openConfirmModal({
      title: "Удалить задачу?",
      children: (
        <Text size="sm">
          Файл «{job.filename}», результаты и ваши правки будут удалены. Подтверждённые коды останутся в эталонных
          примерах системы.
        </Text>
      ),
      labels: { confirm: "Удалить", cancel: "Отмена" },
      confirmProps: { color: "red" },
      onConfirm: () => del.mutate(job.job_id, { onError }),
    });

  return (
    <Menu position="bottom-end" withinPortal>
      <Menu.Target>
        <ActionIcon variant="subtle" color="gray" aria-label="Действия с задачей">
          <IconDots size={18} />
        </ActionIcon>
      </Menu.Target>
      <Menu.Dropdown>
        {isActive(job.status) && (
          <Menu.Item leftSection={<IconPlayerStop size={16} />} onClick={() => cancel.mutate(job.job_id, { onError })}>
            Остановить обработку
          </Menu.Item>
        )}
        <Menu.Item color="red" leftSection={<IconTrash size={16} />} onClick={confirmDelete}>
          Удалить
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}
