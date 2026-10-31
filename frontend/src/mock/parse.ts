/** Разбор загруженного файла в имитации бэкенда: csv и xlsx читаем по-настоящему, pdf — демо-таблица. */
import type { ColumnRole } from "../api/types";
import { SAMPLE_HEADERS, SAMPLE_ROWS } from "./sample";

export type ParsedSheet = { name: string; rows: string[][]; rowCountKnown: boolean };

export async function parseFile(file: File): Promise<ParsedSheet[]> {
  const ext = file.name.toLowerCase().split(".").pop();
  if (ext === "csv") return [{ name: "Лист1", rows: parseCsv(await file.text()), rowCountKnown: true }];
  if (ext === "xlsx") {
    const { default: readXlsxFile, readSheetNames } = await import("read-excel-file");
    const names = await readSheetNames(file);
    const sheets: ParsedSheet[] = [];
    for (const name of names) {
      const rows = await readXlsxFile(file, { sheet: name });
      sheets.push({ name, rows: rows.map((r) => r.map((c) => (c == null ? "" : String(c)))), rowCountKnown: true });
    }
    return sheets;
  }
  // pdf: настоящий бэкенд достаёт таблицы из первых 3 страниц; здесь — демо-спецификация.
  return [{ name: "Таблицы", rows: [SAMPLE_HEADERS, ...SAMPLE_ROWS], rowCountKnown: false }];
}

function parseCsv(text: string): string[][] {
  const firstLine = text.split(/\r?\n/, 1)[0] ?? "";
  const delim = [";", ",", "\t"].sort((a, b) => firstLine.split(b).length - firstLine.split(a).length)[0];
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === delim) {
      row.push(cell);
      cell = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else cell += ch;
  }
  if (cell || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows;
}

/** Строка заголовка — первая строка, где хотя бы две заполненные ячейки и это не числа. */
export function detectHeaderRow(rows: string[][]): number {
  const i = rows
    .slice(0, 20)
    .findIndex((r) => r.filter((c) => c.trim() && Number.isNaN(Number(c.replace(",", ".")))).length >= 2);
  return Math.max(0, i);
}

export function suggestRoles(headers: string[], sample: string[][]): ColumnRole[] {
  const roles = headers.map((h): ColumnRole => {
    const t = h.toLowerCase();
    if (/наимен|товар|предмет|позиц/.test(t)) return "name";
    if (/характ|параметр|техн/.test(t)) return "characteristics";
    if (/опис/.test(t)) return "description";
    if (/кол-?во|колич|qty/.test(t)) return "quantity";
    if (/ед\.?\s*изм|единиц/.test(t)) return "unit";
    return "ignore";
  });
  // Если колонку с наименованием по заголовку не нашли — берём колонку с самым длинным текстом.
  if (!roles.includes("name") && headers.length) {
    const avgLen = headers.map((_, i) => sample.reduce((s, r) => s + (r[i]?.length ?? 0), 0));
    roles[avgLen.indexOf(Math.max(...avgLen))] = "name";
  }
  // Не больше одной колонки количества и единиц, как требует контракт.
  for (const single of ["name", "quantity", "unit"] as const) {
    let seen = false;
    roles.forEach((r, i) => {
      if (r === single) {
        if (seen) roles[i] = "ignore";
        seen = true;
      }
    });
  }
  return roles;
}
