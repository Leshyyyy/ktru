import { createTheme, type MantineColorsTuple } from "@mantine/core";

// Спокойный синий: интерфейс для специалиста по закупкам, а не лендинг.
const brand: MantineColorsTuple = [
  "#eef3ff",
  "#dce4f5",
  "#b6c6e6",
  "#8da7d7",
  "#6b8cca",
  "#557bc3",
  "#4972c0",
  "#3a61aa",
  "#315699",
  "#244a88",
];

export const theme = createTheme({
  primaryColor: "brand",
  colors: { brand },
  fontFamily: "'Inter Variable', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  fontFamilyMonospace: "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
  headings: { fontWeight: "650" },
  defaultRadius: "md",
  cursorType: "pointer",
  components: {
    Card: { defaultProps: { withBorder: true, radius: "md", padding: "lg" } },
    Badge: { defaultProps: { radius: "sm" } },
    Tooltip: { defaultProps: { multiline: true, maw: 320, withArrow: true, openDelay: 250 } },
  },
});
