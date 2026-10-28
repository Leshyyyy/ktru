import "@fontsource-variable/onest";
import "@fontsource-variable/jetbrains-mono";
import "@mantine/core/styles.css";
import "@mantine/dropzone/styles.css";
import "@mantine/notifications/styles.css";
import "./global.css";

import { MantineProvider } from "@mantine/core";
import { ModalsProvider } from "@mantine/modals";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";
import { router } from "./router";
import { cssVariablesResolver, theme } from "./theme";
import { ApiRequestError } from "./api/client";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 4xx не повторяем: 404 от повтора не исчезнет. Сетевые сбои — до двух повторов.
      retry: (n, e) => n < 2 && !(e instanceof ApiRequestError && e.status >= 400 && e.status < 500),
      refetchOnWindowFocus: false,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* Тёмная тема по макетам не нужна — фиксируем светлую. */}
    <MantineProvider theme={theme} forceColorScheme="light" cssVariablesResolver={cssVariablesResolver}>
      <Notifications position="bottom-center" />
      <QueryClientProvider client={queryClient}>
        <ModalsProvider labels={{ confirm: "OK", cancel: "Отмена" }}>
          <RouterProvider router={router} />
        </ModalsProvider>
      </QueryClientProvider>
    </MantineProvider>
  </StrictMode>,
);
