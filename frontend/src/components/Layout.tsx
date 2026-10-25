import {
  ActionIcon,
  Alert,
  AppShell,
  Box,
  Burger,
  Container,
  Group,
  Stack,
  Text,
  Tooltip,
  UnstyledButton,
  useComputedColorScheme,
  useMantineColorScheme,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconFileUpload, IconListDetails, IconMoon, IconChartDots, IconSun } from "@tabler/icons-react";
import { NavLink, Outlet, useLocation } from "react-router";
import { useEffect } from "react";
import { API_MODE } from "../api/client";
import { useHealth } from "../api/hooks";
import classes from "./Layout.module.css";

const NAV = [
  { to: "/", label: "Новая обработка", icon: IconFileUpload, end: true },
  { to: "/jobs", label: "Задачи", icon: IconListDetails, end: false },
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
      <AppShell.Header>
        <Container size="xl" h="100%">
          <Group h="100%" justify="space-between" wrap="nowrap">
            <Group gap="lg" wrap="nowrap">
              <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Меню" />
              <Logo />
              <Group gap={4} visibleFrom="sm">
                {NAV.map((n) => (
                  <NavLink key={n.to} to={n.to} end={n.end} className={classes.link}>
                    {n.label}
                  </NavLink>
                ))}
              </Group>
            </Group>
            <Group gap="xs" wrap="nowrap">
              <HealthDot />
              <ThemeToggle />
            </Group>
          </Group>
        </Container>
      </AppShell.Header>

      <AppShell.Navbar p="sm">
        <Stack gap={4}>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={classes.mobileLink}>
              <n.icon size={18} stroke={1.6} />
              {n.label}
            </NavLink>
          ))}
        </Stack>
      </AppShell.Navbar>

      <AppShell.Main>
        {API_MODE === "memory" && (
          <Box className={classes.demo}>
            <Container size="xl">
              <Text size="xs" ta="center">
                Демо-режим: сервер имитируется в браузере, коды КТРУ вымышленные (99.99.*), данные живут до перезагрузки
                вкладки.
              </Text>
            </Container>
          </Box>
        )}
        <SystemNotReady />
        <Container size="xl" py="lg" px={{ base: "md", sm: "lg" }}>
          <Outlet />
        </Container>
      </AppShell.Main>
    </AppShell>
  );
}

function Logo() {
  return (
    <UnstyledButton component={NavLink} to="/" className={classes.logo}>
      <span className={classes.mark}>КТРУ</span>
      <Text fw={650} size="sm" visibleFrom="xs">
        Классификатор
      </Text>
    </UnstyledButton>
  );
}

function HealthDot() {
  const { data, isError } = useHealth();
  const ok = data?.ready && !isError;
  const label = isError
    ? "Нет связи с сервером"
    : !data
      ? "Проверяем систему…"
      : data.ready
        ? `Система готова. ${data.components.catalog?.detail ?? ""}`
        : "Система готовится";
  return (
    <Tooltip label={label}>
      <Box className={classes.health} data-ok={ok || undefined} data-error={isError || undefined} aria-label={label} />
    </Tooltip>
  );
}

function SystemNotReady() {
  const { data, isError } = useHealth();
  if (isError)
    return (
      <Container size="xl" pt="md">
        <Alert color="red" title="Нет связи с сервером">
          Проверьте, что система запущена (docker-compose up), и обновите страницу.
        </Alert>
      </Container>
    );
  if (data && !data.ready)
    return (
      <Container size="xl" pt="md">
        <Alert color="blue" title="Система готовится">
          Загружаются модели или каталог. Обычно это занимает до минуты — страница обновится сама.
        </Alert>
      </Container>
    );
  return null;
}

function ThemeToggle() {
  const { setColorScheme } = useMantineColorScheme();
  const scheme = useComputedColorScheme("light");
  return (
    <ActionIcon
      variant="subtle"
      color="gray"
      onClick={() => setColorScheme(scheme === "dark" ? "light" : "dark")}
      aria-label="Сменить тему"
    >
      {scheme === "dark" ? <IconSun size={18} /> : <IconMoon size={18} />}
    </ActionIcon>
  );
}
