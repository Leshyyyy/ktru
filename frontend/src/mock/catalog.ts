/**
 * Демо-каталог для имитации бэкенда. Коды ВЫМЫШЛЕННЫЕ (префикс 99.99), это не настоящие коды КТРУ.
 * Первые восемь позиций совпадают с contracts/fixtures/fake_catalog.jsonl (коды и наименования),
 * остальные добавлены, чтобы на демо-спецификации было что искать.
 *
 * keywords — основы слов, по которым имитация «узнаёт» товар (настоящий поиск будет по эмбеддингам).
 * pattern — как найти значение характеристики в тексте позиции.
 */
import type { CatalogItem } from "../api/types";

export type MockChar = { name: string; values: string[]; required: boolean; pattern?: RegExp };
export type MockCatalogItem = Omit<CatalogItem, "characteristics"> & {
  characteristics: MockChar[];
  keywords: string[];
  /** Слова, без которых этот код не подходит (например, «игровой» для игрового ноутбука). */
  requires?: string[];
  /** Слова, при которых код точно не подходит: «картридж для принтера» — это не принтер. */
  excludes?: string[];
  status?: "active" | "archived";
};

// В JS \b понимает только латиницу, поэтому границы слов — через \p{L} и флаг u.
const B = "(?<![\\p{L}\\p{N}])";
const E = "(?![\\p{L}\\p{N}])";
const re = (body: string) => new RegExp(body.replaceAll("<", B).replaceAll(">", E), "iu");

const DIAG = /(\d{2}(?:[.,]\d)?)\s*(?:"|”|''|дюйм)/i;
const RAM = re(String.raw`(\d{1,3})\s*(?:гб|gb)>(?!\s*(?:ssd|hdd))`);
const FORMAT = re(String.raw`<([аa][3-5])>`);
const DENSITY = /(\d{2,3})\s*г\/м/i;
const SIZE = re(String.raw`(?:размер\s*)?<(xs|s|m|l|xl|xxl)>`);
const VOLUME = re(String.raw`(\d+(?:[.,]\d+)?)\s*(?:мл|ml)>`);
const POWER = re(String.raw`(\d+)\s*(?:вт|w|ва|va)>`);
const SPEED = /(\d+)\s*стр/i;
const LENGTH = re(String.raw`(\d+(?:[.,]\d+)?)\s*(?:м|m)>`);
const PORTS = /(\d+)\s*порт/i;
const CAPACITY = re(String.raw`(\d+)\s*(?:гб|gb|тб|tb)>`);
const LITERS = re(String.raw`(\d+(?:[.,]\d+)?)\s*(?:л|l)>`);

export const CATALOG: MockCatalogItem[] = [
  {
    ktru_code: "99.99.01.000-00000001",
    okpd2_code: "99.99.01.000",
    name: "Ноутбук",
    unit: "шт",
    excludes: ["картридж", "тонер", "сумк", "чехол"],
    keywords: ["ноутбук", "notebook", "laptop", "ideapad", "thinkpad", "vivobook", "probook", "aspire"],
    characteristics: [
      { name: "Диагональ экрана", values: ['от 13"'], required: true, pattern: DIAG },
      { name: "Объём ОЗУ", values: ["от 8 ГБ"], required: true, pattern: RAM },
    ],
  },
  {
    ktru_code: "99.99.01.000-00000002",
    okpd2_code: "99.99.01.000",
    name: "Ноутбук игровой",
    unit: "шт",
    keywords: ["ноутбук", "notebook", "игров", "gaming", "rtx"],
    requires: ["игров", "gaming", "rtx"],
    characteristics: [
      { name: "Диагональ экрана", values: ['от 13"'], required: true, pattern: DIAG },
      { name: "Объём ОЗУ", values: ["от 16 ГБ"], required: true, pattern: RAM },
    ],
  },
  {
    ktru_code: "99.99.02.000-00000001",
    okpd2_code: "99.99.02.000",
    name: "Монитор",
    unit: "шт",
    keywords: ["монитор", "monitor", "дисплей"],
    characteristics: [{ name: "Диагональ экрана", values: ['от 21"'], required: true, pattern: DIAG }],
  },
  {
    ktru_code: "99.99.03.000-00000001",
    okpd2_code: "99.99.03.000",
    name: "Принтер лазерный",
    unit: "шт",
    excludes: ["картридж", "тонер", "сумк", "чехол"],
    keywords: ["принтер", "printer", "лазерн", "laserjet"],
    requires: ["лазерн", "laserjet", "laser"],
    characteristics: [
      { name: "Формат печати", values: ["A4"], required: true, pattern: FORMAT },
      { name: "Скорость печати", values: ["от 20 стр/мин"], required: false, pattern: SPEED },
    ],
  },
  {
    ktru_code: "99.99.03.000-00000002",
    okpd2_code: "99.99.03.000",
    name: "Принтер струйный",
    unit: "шт",
    excludes: ["картридж", "тонер", "сумк", "чехол"],
    keywords: ["принтер", "printer", "струйн", "inkjet"],
    requires: ["струйн", "inkjet"],
    characteristics: [{ name: "Формат печати", values: ["A4"], required: true, pattern: FORMAT }],
  },
  {
    ktru_code: "99.99.04.000-00000001",
    okpd2_code: "99.99.04.000",
    name: "Бумага офисная",
    unit: "пач",
    keywords: ["бумаг", "paper", "svetocopy", "снегурочк", "ballet"],
    characteristics: [
      { name: "Формат", values: ["A4", "A3"], required: true, pattern: FORMAT },
      { name: "Плотность", values: ["80 г/м²"], required: true, pattern: DENSITY },
    ],
  },
  {
    ktru_code: "99.99.05.000-00000001",
    okpd2_code: "99.99.05.000",
    name: "Перчатки медицинские",
    unit: "пар",
    keywords: ["перчатк", "нитрил", "латекс"],
    characteristics: [{ name: "Размер", values: ["S", "M", "L", "XL"], required: true, pattern: SIZE }],
  },
  {
    ktru_code: "99.99.06.000-00000001",
    okpd2_code: "99.99.06.000",
    name: "Шприц одноразовый",
    unit: "шт",
    keywords: ["шприц"],
    characteristics: [{ name: "Объём", values: ["1–20 мл"], required: true, pattern: VOLUME }],
  },
  // ---- ниже — только для демо, в fake_catalog.jsonl их нет
  {
    ktru_code: "99.99.03.000-00000003",
    okpd2_code: "99.99.03.000",
    name: "МФУ лазерное",
    unit: "шт",
    excludes: ["картридж", "тонер", "сумк", "чехол"],
    keywords: ["мфу", "многофункциональн", "лазерн"],
    requires: ["мфу", "многофункциональн"],
    characteristics: [
      { name: "Формат печати", values: ["A4"], required: true, pattern: FORMAT },
      { name: "Скорость печати", values: ["от 20 стр/мин"], required: false, pattern: SPEED },
    ],
  },
  {
    ktru_code: "99.99.07.000-00000001",
    okpd2_code: "99.99.07.000",
    name: "Картридж для лазерного принтера",
    unit: "шт",
    keywords: ["картридж", "тонер", "cartridge"],
    requires: ["картридж", "тонер", "cartridge"],
    characteristics: [{ name: "Ресурс", values: ["от 1000 стр"], required: false, pattern: SPEED }],
  },
  {
    ktru_code: "99.99.01.000-00000003",
    okpd2_code: "99.99.01.000",
    name: "Компьютер персональный настольный",
    unit: "шт",
    keywords: ["системн", "компьютер", "пк", "моноблок", "desktop"],
    characteristics: [{ name: "Объём ОЗУ", values: ["от 8 ГБ"], required: true, pattern: RAM }],
  },
  {
    ktru_code: "99.99.08.000-00000001",
    okpd2_code: "99.99.08.000",
    name: "Клавиатура",
    unit: "шт",
    keywords: ["клавиатур", "keyboard"],
    requires: ["клавиатур", "keyboard"],
    characteristics: [],
  },
  {
    ktru_code: "99.99.08.000-00000002",
    okpd2_code: "99.99.08.000",
    name: "Мышь компьютерная",
    unit: "шт",
    keywords: ["мышь", "мыш", "mouse"],
    requires: ["мыш", "mouse"],
    characteristics: [],
  },
  {
    ktru_code: "99.99.09.000-00000001",
    okpd2_code: "99.99.09.000",
    name: "Источник бесперебойного питания",
    unit: "шт",
    keywords: ["ибп", "бесперебойн", "ups"],
    characteristics: [{ name: "Выходная мощность", values: ["от 600 ВА"], required: true, pattern: POWER }],
  },
  {
    ktru_code: "99.99.10.000-00000001",
    okpd2_code: "99.99.10.000",
    name: "Коммутатор сетевой",
    unit: "шт",
    keywords: ["коммутатор", "switch", "свитч"],
    characteristics: [{ name: "Количество портов", values: ["от 8"], required: true, pattern: PORTS }],
  },
  {
    ktru_code: "99.99.10.000-00000002",
    okpd2_code: "99.99.10.000",
    name: "Кабель витая пара",
    unit: "м",
    keywords: ["кабел", "витая", "utp", "ftp", "patch", "патч"],
    characteristics: [{ name: "Длина", values: ["от 1 м"], required: false, pattern: LENGTH }],
  },
  {
    ktru_code: "99.99.11.000-00000001",
    okpd2_code: "99.99.11.000",
    name: "Накопитель флеш USB",
    unit: "шт",
    keywords: ["флеш", "флэш", "usb", "накопител", "flash"],
    characteristics: [{ name: "Объём памяти", values: ["от 16 ГБ"], required: true, pattern: CAPACITY }],
  },
  {
    ktru_code: "99.99.05.000-00000002",
    okpd2_code: "99.99.05.000",
    name: "Маска медицинская",
    unit: "шт",
    keywords: ["маск"],
    characteristics: [],
  },
  {
    ktru_code: "99.99.05.000-00000003",
    okpd2_code: "99.99.05.000",
    name: "Бахилы одноразовые",
    unit: "пар",
    keywords: ["бахил"],
    characteristics: [],
  },
  {
    ktru_code: "99.99.05.000-00000004",
    okpd2_code: "99.99.05.000",
    name: "Бинт марлевый медицинский",
    unit: "шт",
    keywords: ["бинт", "марлев"],
    characteristics: [{ name: "Длина", values: ["5–7 м"], required: true, pattern: LENGTH }],
  },
  {
    ktru_code: "99.99.05.000-00000005",
    okpd2_code: "99.99.05.000",
    name: "Средство антисептическое",
    unit: "шт",
    keywords: ["антисепт", "дезинфиц", "санитайзер"],
    characteristics: [{ name: "Объём", values: ["от 100 мл"], required: true, pattern: VOLUME }],
  },
  {
    ktru_code: "99.99.12.000-00000001",
    okpd2_code: "99.99.12.000",
    name: "Ручка шариковая",
    unit: "шт",
    keywords: ["ручк", "шариков"],
    characteristics: [],
  },
  {
    ktru_code: "99.99.12.000-00000002",
    okpd2_code: "99.99.12.000",
    name: "Папка-регистратор",
    unit: "шт",
    keywords: ["папк", "регистратор"],
    characteristics: [],
  },
  {
    ktru_code: "99.99.12.000-00000003",
    okpd2_code: "99.99.12.000",
    name: "Степлер канцелярский",
    unit: "шт",
    keywords: ["степлер"],
    characteristics: [],
  },
  {
    ktru_code: "99.99.13.000-00000001",
    okpd2_code: "99.99.13.000",
    name: "Средство моющее",
    unit: "шт",
    keywords: ["моющ", "чистящ", "средство"],
    characteristics: [{ name: "Объём", values: ["от 0,5 л"], required: false, pattern: LITERS }],
  },
  {
    ktru_code: "99.99.13.000-00000002",
    okpd2_code: "99.99.13.000",
    name: "Мешки для мусора",
    unit: "уп",
    keywords: ["мешк", "мусор"],
    characteristics: [{ name: "Объём мешка", values: ["30–120 л"], required: true, pattern: LITERS }],
  },
  {
    ktru_code: "99.99.14.000-00000001",
    okpd2_code: "99.99.14.000",
    name: "Лампа светодиодная",
    unit: "шт",
    keywords: ["ламп", "светодиодн", "led"],
    characteristics: [{ name: "Мощность", values: ["от 5 Вт"], required: true, pattern: POWER }],
  },
  {
    ktru_code: "99.99.15.000-00000001",
    okpd2_code: "99.99.15.000",
    name: "Кресло офисное",
    unit: "шт",
    keywords: ["кресл"],
    characteristics: [],
  },
  {
    ktru_code: "99.99.15.000-00000002",
    okpd2_code: "99.99.15.000",
    name: "Стол письменный",
    unit: "шт",
    keywords: ["стол"],
    characteristics: [],
  },
  {
    ktru_code: "99.99.16.000-00000001",
    okpd2_code: "99.99.16.000",
    name: "Огнетушитель порошковый",
    unit: "шт",
    keywords: ["огнетушител", "оп-"],
    status: "archived",
    characteristics: [{ name: "Масса заряда", values: ["2–10 кг"], required: true, pattern: /(\d+)\s*кг/i }],
  },
];

export const CATALOG_VERSION_ID = "5d2e9b34-1f7a-4e60-b0c4-6a8d3f1e2c77";

export function toPublic(c: MockCatalogItem): CatalogItem {
  return {
    ktru_code: c.ktru_code,
    okpd2_code: c.okpd2_code,
    name: c.name,
    description: c.description ?? null,
    unit: c.unit ?? null,
    characteristics: c.characteristics.map(({ name, values, required }) => ({ name, values, required })),
  };
}

export const byCode = new Map(CATALOG.map((c) => [c.ktru_code, c]));
