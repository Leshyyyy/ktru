import { Alert, AppShell, Box, Burger, Group, Popover, Stack, Text, UnstyledButton } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconChartDots, IconChevronDown, IconFileUpload, IconListDetails } from "@tabler/icons-react";
import { useEffect } from "react";
import { NavLink, Outlet, useLocation } from "react-router";
import { API_MODE } from "../api/client";
import { useCatalogVersions, useConfig, useHealth } from "../api/hooks";
import { plural } from "../lib/format";
import classes from "./Layout.module.css";

// Порядок как в макете: сначала список задач — с него специалист начинает день.
const NAV = [
  { to: "/jobs", label: "Задачи", icon: IconListDetails, end: false },
  { to: "/", label: "Новая загрузка", icon: IconFileUpload, end: true },
  { to: "/quality", label: "Качество", icon: IconChartDots, end: false },
];

export function Layout() {
  const [opened, { toggle, close }] = useDisclosure(false);
  const location = useLocation();
  useEffect(close, [location.pathname, close]);

  return (
    <AppShell
      header={{ height: 60 }}
      navbar={{ width: 260, breakpoint: "sm", collapsed: { desktop: true, mobile: !opened } }}
      padding={0}
    >
      <AppShell.Header className={classes.header}>
        <Group h="100%" px={{ base: "md", md: 32 }} gap={36} wrap="nowrap">
          <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Меню" />
          <Logo />
          <nav className={classes.nav}>
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end} className={classes.link}>
                {n.label}
              </NavLink>
            ))}
          </nav>
          <Group gap="xs" wrap="nowrap" ml="auto" visibleFrom="md">
            <SystemPill />
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm" style={{ overflow: "hidden" }}>
        <Stack gap={4}>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={classes.mobileLink}>
              <n.icon size={18} stroke={1.6} />
              {n.label}
            </NavLink>
          ))}
          <Box pt="md">
            <SystemPill />
          </Box>
        </Stack>
      </AppShell.Navbar>

      <AppShell.Main>
        {API_MODE === "memory" && (
          <Box className={classes.demo}>
            <Text size="xs" ta="center">
              Демо-режим: сервер имитируется в браузере, коды КТРУ вымышленные (99.99.*), данные живут до перезагрузки
              вкладки.
            </Text>
          </Box>
        )}
        <SystemNotReady />
        <Box className={classes.page}>
          <Outlet />
        </Box>
      </AppShell.Main>
    </AppShell>
  );
}

function Logo() {
  return (
    <UnstyledButton component={NavLink} to="/jobs" className={classes.logo} aria-label="Классификатор КТРУ">
      <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden>
        <rect x="1" y="1" width="11" height="11" rx="3" fill="#0E6B5C" />
        <rect x="14" y="1" width="11" height="11" rx="3" fill="#E4F7A8" stroke="#C9E57A" />
        <rect x="1" y="14" width="11" height="11" rx="3" fill="#DCEFE9" stroke="#BFE3D8" />
        <rect x="14" y="14" width="11" height="11" rx="3" fill="#0E6B5C" opacity=".55" />
      </svg>
      <Text fw={600} size="md" visibleFrom="xs" style={{ letterSpacing: "-0.01em" }}>
        Классификатор КТРУ
      </Text>
    </UnstyledButton>
  );
}

const dateFmt = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });

const RERANKER: Record<string, string> = {
  none: "не используется",
  cross_encoder: "отдельной моделью",
  llm: "языковой моделью",
};

/**
 * «О системе»: готовность, каталог и режим работы. В шапке — только точка готовности и размер каталога,
 * остальное (CPU, реранкер, LLM) — по клику, человеческими словами: специалисту по закупкам эти термины не нужны.
 */
function SystemPill() {
  const health = useHealth();
  const versions = useCatalogVersions();
  const mode = useConfig().data?.mode;
  const v = versions.data?.items.find((x) => x.status === "ready");
  const state = health.isError ? "error" : health.data?.ready ? "ok" : "wait";
  // Пока у бэкенда нет /catalog/versions, берём описание каталога из /health.
  const label = v
    ? `Каталог: ${v.items_count.toLocaleString("ru-RU")} ${plural(v.items_count, "код", "кода", "кодов")}`
    : health.data?.components.catalog?.detail
      ? `Каталог: ${health.data.components.catalog.detail}`
      : versions.isLoading || health.isLoading
        ? "Каталог…"
        : "Каталог не загружен";
  const status =
    state === "error"
      ? "Нет связи с сервером"
      : state === "wait"
        ? "Готовится: загружаются модели или каталог"
        : "Готова к работе";
  return (
    <Popover position="bottom-end" width={340} shadow="md" withArrow>
      <Popover.Target>
        <UnstyledButton className={`${classes.pill} ${classes.pillBtn}`} aria-label={`О системе. ${status}. ${label}`}>
          <i className={classes.dot} data-state={state} aria-hidden />
          {label}
          <IconChevronDown size={14} aria-hidden />
        </UnstyledButton>
      </Popover.Target>
      <Popover.Dropdown>
        <Text fw={600} mb="xs">
          О системе
        </Text>
        <dl className={classes.about}>
          <dt>Состояние</dt>
          <dd>{status}</dd>
          <dt>Каталог КТРУ</dt>
          <dd>
            {v
              ? `${v.items_count.toLocaleString("ru-RU")} ${plural(v.items_count, "код", "кода", "кодов")}, загружен ${dateFmt.format(new Date(v.loaded_at))}`
              : label}
          </dd>
          {mode && (
            <>
              <dt>Подбор кода</dt>
              <dd>
                {mode.llm_enabled
                  ? "поиск по каталогу, сложные позиции разбирает языковая модель"
                  : "поиск по каталогу и правила, без языковой модели"}
              </dd>
              <dt>Уточнение вариантов</dt>
              <dd>{RERANKER[mode.reranker ?? "none"] ?? mode.reranker}</dd>
              <dt>Вычисления</dt>
              <dd>{mode.device === "cuda" ? "на видеокарте" : "на процессоре, видеокарта не нужна"}</dd>
            </>
          )}
        </dl>
      </Popover.Dropdown>
    </Popover>
  );
}

function SystemNotReady() {
  const { data, isError } = useHealth();
  if (isError)
    return (
      <Box className={classes.page} pb={0}>
        <Alert color="red" title="Нет связи с сервером">
          Проверьте, что система запущена (docker-compose up), и обновите страницу.
        </Alert>
      </Box>
    );
  if (data && !data.ready)
    return (
      <Box className={classes.page} pb={0}>
        <Alert color="yellow" title="Система готовится: загружаются модели">
          Обычно это занимает до минуты. Страница обновится сама.
        </Alert>
      </Box>
    );
  return null;
}
