import { Alert, AppShell, Box, Burger, Group, Stack, Text, Tooltip, UnstyledButton } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconChartDots, IconFileUpload, IconListDetails } from "@tabler/icons-react";
import { useEffect } from "react";
import { NavLink, Outlet, useLocation } from "react-router";
import { API_MODE } from "../api/client";
import { useCatalogVersions, useConfig, useHealth } from "../api/hooks";
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
            <CatalogPill />
            <ModePill />
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
            <CatalogPill />
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
      <Text fw={700} size="md" visibleFrom="xs" style={{ letterSpacing: "-0.01em" }}>
        Классификатор КТРУ
      </Text>
    </UnstyledButton>
  );
}

const dateFmt = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });

/** Версия каталога, с которой работают новые задачи, и готовность системы (точка). */
function CatalogPill() {
  const health = useHealth();
  const versions = useCatalogVersions();
  const v = versions.data?.items.find((x) => x.status === "ready");
  const state = health.isError ? "error" : health.data?.ready ? "ok" : "wait";
  // Пока у бэкенда нет /catalog/versions, берём описание каталога из /health.
  const label = v
    ? `Каталог от ${dateFmt.format(new Date(v.loaded_at))} · ${v.items_count.toLocaleString("ru-RU")} кодов`
    : health.data?.components.catalog?.detail
      ? `Каталог: ${health.data.components.catalog.detail}`
      : versions.isLoading || health.isLoading
        ? "Каталог…"
        : "Каталог не загружен";
  const hint =
    state === "error"
      ? "Нет связи с сервером"
      : state === "wait"
        ? "Система готовится: загружаются модели или каталог"
        : `Система готова. ${v?.note ?? ""}`;
  return (
    <Tooltip label={hint}>
      <span className={classes.pill}>
        <i className={classes.dot} data-state={state} aria-hidden />
        {label}
      </span>
    </Tooltip>
  );
}

/** Режим работы из /config: где считается, какой реранкер, включена ли LLM. */
function ModePill() {
  const mode = useConfig().data?.mode;
  if (!mode) return null;
  const reranker = mode.reranker === "none" ? "без реранкера" : mode.reranker === "llm" ? "LLM-реранкер" : "реранкер";
  return (
    <Tooltip label="Режим работы системы: устройство, переранжирование кандидатов и языковая модель">
      <span className={classes.pill}>
        {(mode.device ?? "cpu").toUpperCase()} · {reranker} · LLM {mode.llm_enabled ? "вкл." : "выкл."}
      </span>
    </Tooltip>
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
          Обычно это занимает до минуты — страница обновится сама.
        </Alert>
      </Box>
    );
  return null;
}
