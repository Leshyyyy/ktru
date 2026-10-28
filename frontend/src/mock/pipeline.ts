/**
 * Имитация ML-конвейера для демо интерфейса. Похожа на contracts/stub_pipeline.py, но «узнаёт» товар
 * по ключевым словам, чтобы в демо ноутбук получал код ноутбука, а не случайный.
 * К настоящему ML отношения не имеет: никакого качества здесь нет, только правдоподобная картинка.
 */
import type { CharacteristicMatch, ExtractedAttributes, ItemFlag, Prediction } from "../api/types";
import { CATALOG, type MockCatalogItem } from "./catalog";

export type MockResult = {
  extracted: ExtractedAttributes | null;
  predictions: Prediction[];
  flags: ItemFlag[];
  path: "fast" | "rerank" | null;
  norm_key: string | null;
  error: string | null;
};

const BRANDS = [
  "Lenovo",
  "HP",
  "Dell",
  "ASUS",
  "Acer",
  "Samsung",
  "Canon",
  "Xerox",
  "LG",
  "Svetocopy",
  "Brother",
  "Kyocera",
  "Pantum",
  "AOC",
  "Philips",
  "Logitech",
  "Defender",
  "APC",
  "Ippon",
  "TP-Link",
  "D-Link",
  "Kingston",
  "Transcend",
  "SFM",
  "Ballet",
  "Снегурочка",
  "Erich Krause",
  "Berlingo",
  "Kangaro",
  "Bic",
  "Gauss",
  "Navigator",
  "Бурёнка",
  "Aquaminerale",
  "Мир чистоты",
  "Paclan",
  "Merida",
  "Chairman",
  "MSI",
];

// Токен из латиницы и цифр, в котором есть и то и другое: 15IAU7, B230, i5-1235U (как в stub_pipeline.py).
const MODEL_RE = /\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9-]{3,}\b/;

/** FNV-1a: детерминированный «случай» из текста, чтобы одинаковые строки всегда давали одинаковый ответ. */
export function hash(text: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

const tokens = (t: string) =>
  t
    .toLowerCase()
    .replace(/ё/g, "е")
    .split(/[^a-zа-я0-9-]+/i)
    .filter(Boolean);

function extract(text: string, columns: Record<string, string>): ExtractedAttributes {
  const brand = BRANDS.find((b) => new RegExp(`(^|[^\\p{L}])${escapeRe(b)}([^\\p{L}]|$)`, "iu").test(text)) ?? null;
  const modelMatch = MODEL_RE.exec(brand ? text.slice(text.toLowerCase().indexOf(brand.toLowerCase())) : text);
  let head = (columns.name || text).split(",")[0];
  if (brand) head = head.split(new RegExp(escapeRe(brand), "i"))[0];
  const name = (head.trim() || text.trim()).slice(0, 120);

  const characteristics: Record<string, string> = {};
  const diag = /(\d{2}(?:[.,]\d)?)\s*(?:"|”|дюйм)/i.exec(text);
  if (diag) characteristics["Диагональ"] = `${diag[1]}"`;
  const ram = /(\d{1,3})\s*(?:гб|gb)(?![\p{L}\p{N}])/iu.exec(text);
  if (ram) characteristics["Объём"] = `${ram[1]} ГБ`;
  const fmt = /(?<![\p{L}\p{N}])([аa][3-5])(?![\p{L}\p{N}])/iu.exec(text);
  if (fmt) characteristics["Формат"] = fmt[1].toUpperCase().replace("А", "A");
  const cpu = /\b(core\s*i[3579][-\s]?\w*|ryzen\s*\d\s*\w*)/i.exec(text);
  if (cpu) characteristics["Процессор"] = cpu[1];

  const qty = Number(String(columns.quantity ?? "").replace(",", "."));
  return {
    name,
    brand,
    model: modelMatch ? modelMatch[0] : null,
    quantity: Number.isFinite(qty) && qty > 0 ? qty : null,
    unit: columns.unit?.trim() || null,
    characteristics,
    method: "rules",
    field_confidence: { name: 0.9, brand: brand ? 0.85 : 0, model: modelMatch ? 0.7 : 0 },
  };
}

function escapeRe(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function score(c: MockCatalogItem, toks: string[]): number {
  const has = (stem: string) => toks.some((t) => t.startsWith(stem));
  if (c.requires && !c.requires.some(has)) return 0;
  if (c.excludes?.some(has)) return 0;
  return c.keywords.filter(has).length + (c.requires ? 0.5 : 0);
}

function matchChars(c: MockCatalogItem, text: string): CharacteristicMatch[] {
  return c.characteristics.map((ch) => {
    const m = ch.pattern?.exec(text);
    return {
      name: ch.name,
      expected: ch.values.join(", "),
      found: m ? m[0].trim() : null,
      status: m ? "match" : "missing",
      required: ch.required,
    };
  });
}

export function classify(text: string, columns: Record<string, string> = {}): MockResult {
  const clean = text.trim();
  if (clean.includes("__fail__") || clean.replace(/[^\p{L}\p{N}]/gu, "").length < 6) {
    return {
      extracted: null,
      predictions: [],
      flags: [],
      path: null,
      norm_key: null,
      error: "Не удалось разобрать позицию: в строке нет описания товара.",
    };
  }
  const h = hash(clean);
  const toks = tokens(clean);
  const ranked = CATALOG.map((c) => ({ c, s: score(c, toks) })).sort(
    (a, b) => b.s - a.s || hash(a.c.ktru_code + clean) - hash(b.c.ktru_code + clean),
  );

  const s1 = ranked[0].s;
  const s2 = ranked[1].s;
  // Уверенность первого кода: чем больше отрыв от второго, тем выше. Немного «шума» из хэша,
  // чтобы в таблице были все три цвета, как у настоящего конвейера.
  let top: number;
  if (s1 === 0)
    top = 0.18 + (h % 27) / 100; // 0.18–0.44: ничего не узнали
  else if (s1 - s2 >= 1 && h % 9 !== 0)
    top = 0.86 + (h % 12) / 100; // 0.86–0.97
  else top = 0.52 + (h % 31) / 100; // 0.52–0.82: близкие кандидаты
  const rest = 1 - top;
  const confs = [top, Math.min(rest * 0.6, top * 0.9), Math.min(rest * 0.25, top * 0.45)];

  const extracted = extract(clean, columns);
  const predictions: Prediction[] = ranked.slice(0, 3).map(({ c, s }, i) => {
    const matches = matchChars(c, clean);
    const req = matches.filter((m) => m.required);
    const okReq = req.filter((m) => m.status === "match").length;
    return {
      rank: i + 1,
      ktru_code: c.ktru_code,
      okpd2_code: c.okpd2_code,
      name: c.name,
      confidence: Math.round(confs[i] * 1000) / 1000,
      explanation:
        i === 0
          ? s1 === 0
            ? "Наименование не похоже ни на одну позицию каталога — код подобран наугад, нужна проверка."
            : req.length
              ? `Совпало наименование и ${okReq} из ${req.length} обязательных характеристик.`
              : "Совпало наименование; обязательных характеристик у кода нет."
          : null,
      why_not_chosen:
        i === 0
          ? null
          : s === 0
            ? `В тексте нет признаков товара «${c.name}».`
            : `Наименование совпало слабее, чем у «${ranked[0].c.name}».`,
      characteristic_matches: i === 0 ? matches : [],
    };
  });

  const flags: ItemFlag[] = [];
  if (predictions[0].characteristic_matches?.some((m) => m.required && m.status === "missing"))
    flags.push("required_missing");
  if (ranked[0].c.status === "archived") flags.push("archived_code");
  if (!extracted.brand && !extracted.model && h % 11 === 0) flags.push("low_extraction_confidence");

  const n = (s: string) => s.toLowerCase().trim();
  return {
    extracted,
    predictions,
    flags,
    path: top >= 0.85 ? "fast" : "rerank",
    norm_key:
      extracted.brand && extracted.model ? `${n(extracted.name)}|${n(extracted.brand)}|${n(extracted.model)}` : null,
    error: null,
  };
}
