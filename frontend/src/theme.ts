import { createTheme, type CSSVariablesResolver, type MantineColorsTuple } from "@mantine/core";

/**
 * Тема по макетам «Экраны.html» (docs/team): тёмно-бирюзовый #0E6B5C, фон #F4F6F3, линии #DDE3DF,
 * скругление 10 px, Onest для интерфейса и JetBrains Mono для кодов и чисел. Только светлая тема.
 * Остальные цвета макета (зоны уверенности, статусы) — переменные --k-* в global.css.
 */
const primary: MantineColorsTuple = [
  "#eaf6f2",
  "#dcefe9", // --primary-soft
  "#b9e0d4",
  "#90cdbb",
  "#66b8a1",
  "#3fa287",
  "#1f8a70",
  "#0e6b5c", // --primary: основной оттенок (primaryShade = 7)
  "#0c5a4e",
  "#0a3f37", // --primary-deep
];

export const theme = createTheme({
  primaryColor: "primary",
  primaryShade: 7,
  colors: { primary },
  black: "#16201C",
  fontFamily: "'Onest Variable', system-ui, -apple-system, 'Segoe UI', sans-serif",
  fontFamilyMonospace: "'JetBrains Mono Variable', ui-monospace, Consolas, monospace",
  headings: { fontWeight: "600" },
  radius: { xs: "5px", sm: "7px", md: "10px", lg: "12px", xl: "16px" },
  defaultRadius: "md",
  cursorType: "pointer",
  components: {
    Card: { defaultProps: { withBorder: true, radius: "md", padding: "lg" } },
    Badge: { defaultProps: { radius: "sm" } },
    Button: { defaultProps: { radius: 9 } },
    Tooltip: { defaultProps: { multiline: true, maw: 320, withArrow: true, openDelay: 250 } },
    // Заголовок плашки цветом текста: оранжевый и жёлтый Mantine на светлом фоне плашки не дают контраста 4.5:1,
    // а смысл и так несут фон плашки и сами слова.
    Alert: { styles: { title: { color: "var(--k-ink)" } } },
  },
});

/** Нейтральные цвета макета вместо серых Mantine: текст, приглушённый текст, линии, фон страницы. */
export const cssVariablesResolver: CSSVariablesResolver = () => ({
  variables: {},
  light: {
    "--mantine-color-body": "#F4F6F3",
    "--mantine-color-text": "#16201C",
    "--mantine-color-dimmed": "#5E6B66",
    "--mantine-color-default-border": "#DDE3DF",
    "--mantine-color-placeholder": "#8A9590",
  },
  dark: {},
});
