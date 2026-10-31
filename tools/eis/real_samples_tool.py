#!/usr/bin/env python3
"""
real_samples_tool.py — утилита валидации и сборки тестового датасета real_samples.jsonl.

Поддерживает два режима работы:
1. csv — сборка и валидация из заполненного CSV-файла (например, real_hunt_list.csv)
2. xml — автоматический разбор скачанных XML-выгрузок извещений ЕИС (epNotificationEF2020*.xml)

Правила проверки (по ТЗ):
- Номер извещения состоит из 19 цифр (в source пишется: «извещение <19 цифр>»)
- Код КТРУ активный, не шаблонный (не оканчивается на -00000000)
- Если доступен каталог (catalog_full.jsonl / catalog_org.jsonl) — проверяется наличие и статус кода
- Не более 5 строк на один код КТРУ (из разных извещений)
- Нет дубликатов (одинаковый код + одинаковый текст)
- Длина текста не короче 15 символов
- Нумерация позиций: r-0001, r-0002, ...
- split = "test"
- Текст не изменяется (только очистка лишних пробелов)

Использование:
    python -m tools.eis.real_samples_tool csv real_hunt_list.csv [--catalog catalog_full.jsonl] [--out real_samples.jsonl]
    python -m tools.eis.real_samples_tool xml ПАПКА_С_XML [--catalog catalog_full.jsonl] [--out real_samples.jsonl]
"""

import argparse
import csv
import glob
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple, Any

KTRU_CODE_PATTERN = re.compile(r"^\d{2}\.\d{2}\.\d{2}\.\d{3}-\d{8}$")
NOTICE_PATTERN = re.compile(r"\b\d{19}\b")


def clean_spaces(text: str) -> str:
    """Нормализует пробелы и переводы строк, не меняя сам текст."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def normalize_notice(notice: str) -> Optional[str]:
    """Извлекает 19 цифр из номера извещения и возвращает строку «извещение <19 цифр>»."""
    if not notice:
        return None
    m = NOTICE_PATTERN.search(str(notice).strip())
    if m:
        return f"извещение {m.group(0)}"
    return None


def load_catalog(catalog_path: Optional[str]) -> Tuple[Dict[str, str], Optional[str]]:
    """
    Загружает активные коды КТРУ из каталога (если найден).
    Возвращает словарь {code: status} и путь к найденному каталогу.
    """
    candidates = []
    if catalog_path:
        candidates.append(catalog_path)
    else:
        # Стандартные пути поиска каталога
        candidates.extend([
            "catalog_full.jsonl",
            "catalog_org.jsonl",
            "data/catalog_full.jsonl",
            "data/catalog_org.jsonl",
            "../catalog_full.jsonl",
            "../data/catalog_full.jsonl",
        ])

    found_path = None
    for cand in candidates:
        if os.path.isfile(cand):
            found_path = cand
            break

    if not found_path:
        return {}, None

    catalog = {}
    with open(found_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                code = row.get("code") or row.get("ktru_code")
                status = row.get("status") or row.get("state") or "ACTIVE"
                if code:
                    catalog[code.strip()] = str(status).upper()
            except Exception:
                continue
    return catalog, found_path


class DatasetBuilder:
    def __init__(self, catalog: Dict[str, str], catalog_source: Optional[str] = None):
        self.catalog = catalog
        self.catalog_source = catalog_source
        self.accepted: List[Dict[str, Any]] = []
        self.rejected: List[Dict[str, Any]] = []
        self.code_counts: Dict[str, int] = defaultdict(int)
        self.seen_signatures: Set[Tuple[str, str]] = set()
        self.skipped_empty_count = 0

    def add_candidate(
        self,
        raw_text: str,
        ktru_code: str,
        notice: str,
        origin_ref: str,
        area: Optional[str] = None,
    ) -> bool:
        """
        Проверяет и добавляет запись кандидата.
        Возвращает True, если позиция принята, иначе False.
        """
        clean_text = clean_spaces(raw_text)
        code = clean_spaces(ktru_code)
        
        # 1. Проверка на пустые данные / пропуск
        if not clean_text and not notice:
            self.skipped_empty_count += 1
            return False

        # 2. Проверка длины текста
        if len(clean_text) < 15:
            self.rejected.append({
                "origin": origin_ref,
                "code": code,
                "notice": notice,
                "reason": f"Текст слишком короткий ({len(clean_text)} символов, минимум 15)",
                "text": clean_text,
            })
            return False

        # 3. Проверка номера извещения
        normalized_notice_str = normalize_notice(notice)
        if not normalized_notice_str:
            self.rejected.append({
                "origin": origin_ref,
                "code": code,
                "notice": notice,
                "reason": f"Некорректный номер извещения (ожидалось 19 цифр, получено: '{notice}')",
                "text": clean_text,
            })
            return False

        # 4. Проверка формата кода КТРУ
        if not KTRU_CODE_PATTERN.match(code):
            self.rejected.append({
                "origin": origin_ref,
                "code": code,
                "notice": notice,
                "reason": f"Неверный формат кода КТРУ ('{code}')",
                "text": clean_text,
            })
            return False

        # 5. Проверка на укрупнённый / шаблонный код
        if code.endswith("-00000000"):
            self.rejected.append({
                "origin": origin_ref,
                "code": code,
                "notice": notice,
                "reason": "Укрупнённый/шаблонный код КТРУ (оканчивается на -00000000)",
                "text": clean_text,
            })
            return False

        # 6. Проверка по каталогу (если каталог загружен)
        if self.catalog:
            if code not in self.catalog:
                self.rejected.append({
                    "origin": origin_ref,
                    "code": code,
                    "notice": notice,
                    "reason": "Код КТРУ отсутствует в каталоге",
                    "text": clean_text,
                })
                return False
            status = self.catalog[code]
            if "ARCHIV" in status or "TERMINAT" in status or "CANCEL" in status:
                self.rejected.append({
                    "origin": origin_ref,
                    "code": code,
                    "notice": notice,
                    "reason": f"Код КТРУ помечен в каталоге как устаревший/архивный (статус: {status})",
                    "text": clean_text,
                })
                return False

        # 7. Проверка на дубликат (код + текст)
        sig = (code, clean_text.lower())
        if sig in self.seen_signatures:
            self.rejected.append({
                "origin": origin_ref,
                "code": code,
                "notice": notice,
                "reason": "Дубликат (такой же текст с данным кодом КТРУ уже добавлен)",
                "text": clean_text,
            })
            return False

        # 8. Проверка квоты: не более 5 строк на один код КТРУ
        if self.code_counts[code] >= 5:
            self.rejected.append({
                "origin": origin_ref,
                "code": code,
                "notice": notice,
                "reason": f"Превышен лимит (уже набрано 5 строк для кода {code})",
                "text": clean_text,
            })
            return False

        # Успешное принятие
        item_id = f"r-{len(self.accepted) + 1:04d}"
        self.code_counts[code] += 1
        self.seen_signatures.add(sig)

        accepted_item = {
            "id": item_id,
            "raw_text": clean_text,
            "expected_ktru_code": code,
            "source": normalized_notice_str,
            "split": "test",
        }
        if area:
            accepted_item["_area"] = area

        self.accepted.append(accepted_item)
        return True

    def export_results(self, jsonl_path: str, report_path: str):
        """Сохраняет итоговый real_samples.jsonl и real_samples_report.txt."""
        # 1. Запись JSONL
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for item in self.accepted:
                # Служебные поля (начинающиеся с _) исключаем из итогового JSONL
                clean_dict = {k: v for k, v in item.items() if not k.startswith("_")}
                f.write(json.dumps(clean_dict, ensure_ascii=False) + "\n")

        # 2. Формирование отчёта
        reasons_count: Dict[str, int] = defaultdict(int)
        for r in self.rejected:
            reasons_count[r["reason"]] += 1

        areas_count: Dict[str, int] = defaultdict(int)
        for a in self.accepted:
            area = a.get("_area", "Не указана")
            areas_count[area] += 1

        report_lines = [
            "============================================================",
            " ОТЧЁТ О СБОРКЕ И ВАЛИДАЦИИ real_samples.jsonl",
            "============================================================",
            f"Каталог КТРУ: {self.catalog_source or 'НЕ ЗАГРУЖЕН (проверка по статусу каталога пропущена)'}",
            f"Всего принято строк:        {len(self.accepted)}",
            f"Уникальных кодов КТРУ:      {len(self.code_counts)}",
            f"Отклонено строк:            {len(self.rejected)}",
            f"Пропущено пустых строк:     {self.skipped_empty_count}",
            "",
            "--- Распределение по отраслям/областям ---",
        ]
        if areas_count:
            for area, cnt in sorted(areas_count.items(), key=lambda x: -x[1]):
                report_lines.append(f"  * {area}: {cnt} позиций")
        else:
            report_lines.append("  (области не указаны)")

        report_lines.extend([
            "",
            "--- Причины отклонения кандидатов ---",
        ])
        if reasons_count:
            for reason, cnt in sorted(reasons_count.items(), key=lambda x: -x[1]):
                report_lines.append(f"  [{cnt:3d}] {reason}")
        else:
            report_lines.append("  Отклонений нет!")

        report_lines.extend([
            "",
            "--- Детализация отклонённых строк (первые 50) ---",
        ])
        for r in self.rejected[:50]:
            report_lines.append(
                f"- Источник: {r['origin']} | Код: {r['code']} | Извещение: {r['notice']}\n"
                f"  Причина: {r['reason']}\n"
                f"  Текст: {r['text'][:90]}..."
            )
        if len(self.rejected) > 50:
            report_lines.append(f"... и ещё {len(self.rejected) - 50} отклонённых строк.")

        report_lines.extend([
            "",
            "============================================================",
            f"Итоговый датасет записан в: {os.path.abspath(jsonl_path)}",
            f"Отчёт сохранён в:           {os.path.abspath(report_path)}",
            "============================================================",
        ])

        report_text = "\n".join(report_lines)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_text)

        print(report_text)


def process_csv(csv_path: str, builder: DatasetBuilder):
    """Разбирает CSV-файл (например, real_hunt_list.csv)."""
    if not os.path.isfile(csv_path):
        print(f"Ошибка: CSV файл '{csv_path}' не найден!", file=sys.stderr)
        sys.exit(1)

    # Определяем кодировку
    content = None
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            with open(csv_path, "r", encoding=enc) as f:
                content = f.read()
                break
        except UnicodeDecodeError:
            continue

    if content is None:
        print(f"Ошибка: не удалось прочесть '{csv_path}' (проверены utf-8, cp1251)", file=sys.stderr)
        sys.exit(1)

    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        print(f"Ошибка: CSV файл '{csv_path}' пуст!", file=sys.stderr)
        sys.exit(1)

    # Определяем разделитель
    first_line = lines[0]
    delimiter = ";" if first_line.count(";") >= first_line.count(",") else ","

    reader = csv.reader(lines, delimiter=delimiter)
    headers = [h.strip().lower() for h in next(reader)]

    # Ищем соответствие колонок
    col_code = -1
    col_text = -1
    col_notice = -1
    col_area = -1
    col_found = -1

    for idx, h in enumerate(headers):
        if "ktru" in h or h in ("код", "код ктру", "код_ктру"):
            col_code = idx
        elif "raw_text" in h or "характеристик" in h or "текст" in h:
            col_text = idx
        elif "notice" in h or "извещен" in h or "номер" in h:
            col_notice = idx
        elif "область" in h or "area" in h or "категория" in h:
            col_area = idx
        elif "нашёл" in h or "нашел" in h or "found" in h:
            col_found = idx

    # Запасной вариант, если заголовки точно как в real_hunt_list.csv:
    # область;ktru_code;наименование;okpd2;ссылка_поиска_в_ЕИС;notice (номер извещения);raw_text;нашёл? (да/нет)
    if col_code == -1 and len(headers) > 1:
        col_code = 1
    if col_notice == -1 and len(headers) > 5:
        col_notice = 5
    if col_text == -1 and len(headers) > 6:
        col_text = 6

    row_num = 1
    for row in reader:
        row_num += 1
        if not any(row):
            continue

        # Проверка флага "нашёл?": если "нет" — пропускаем без ошибок
        if col_found != -1 and col_found < len(row):
            found_val = row[col_found].strip().lower()
            if found_val in ("нет", "no", "0", "false"):
                builder.skipped_empty_count += 1
                continue

        code = row[col_code].strip() if col_code != -1 and col_code < len(row) else ""
        text = row[col_text].strip() if col_text != -1 and col_text < len(row) else ""
        notice = row[col_notice].strip() if col_notice != -1 and col_notice < len(row) else ""
        area = row[col_area].strip() if col_area != -1 and col_area < len(row) else ""

        if not text and not notice:
            builder.skipped_empty_count += 1
            continue

        builder.add_candidate(
            raw_text=text,
            ktru_code=code,
            notice=notice,
            origin_ref=f"{os.path.basename(csv_path)}:строка {row_num}",
            area=area,
        )


def _strip_ns(tag: str) -> str:
    """Удаляет пространство имён XML {http://...}tag -> tag."""
    return tag.rsplit("}", 1)[-1]


def _find_child(element: ET.Element, name: str) -> Optional[ET.Element]:
    for c in element:
        if _strip_ns(c.tag) == name:
            return c
    return None


def _get_child_text(element: ET.Element, name: str) -> str:
    c = _find_child(element, name)
    return (c.text or "").strip() if c is not None else ""


def _parse_xml_file(xml_path: str, builder: DatasetBuilder):
    """Разбирает одиночный XML файл извещения ЕИС."""
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except Exception as e:
        builder.rejected.append({
            "origin": os.path.basename(xml_path),
            "code": "N/A",
            "notice": "N/A",
            "reason": f"Ошибка парсинга XML: {e}",
            "text": "",
        })
        return

    # Поиск номера извещения
    notice_number = ""
    for elem in root.iter():
        tag = _strip_ns(elem.tag)
        if tag in ("notificationNumber", "purchaseNumber"):
            val = (elem.text or "").strip()
            if NOTICE_PATTERN.match(val):
                notice_number = val
                break

    if not notice_number:
        # Пробуем извлечь из имени файла (часто имя содержит номер извещения)
        m = NOTICE_PATTERN.search(os.path.basename(xml_path))
        if m:
            notice_number = m.group(0)

    # Поиск объектов закупки
    # В схемах ЕИС (epNotificationEF2020) объекты лежат в purchaseObjects -> purchaseObject
    # Внутри каждого объекта может быть блок KTRU или код КТРУ
    for elem in root.iter():
        tag = _strip_ns(elem.tag)
        if tag in ("purchaseObject", "drugPurchaseObjectInfo", "item"):
            # Извлекаем код КТРУ
            ktru_code = ""
            name = _get_child_text(elem, "name") or _get_child_text(elem, "okpd2Name")
            
            # Ищем блок KTRU
            ktru_elem = _find_child(elem, "KTRU") or _find_child(elem, "ktru")
            if ktru_elem is not None:
                ktru_code = _get_child_text(ktru_elem, "code") or _get_child_text(ktru_elem, "ktruCode")
                ktru_name = _get_child_text(ktru_elem, "name")
                if ktru_name and not name:
                    name = ktru_name

            if not ktru_code:
                # Поиск прямого тега ktruCode
                ktru_code = _get_child_text(elem, "ktruCode") or _get_child_text(elem, "code")

            if not ktru_code or not KTRU_CODE_PATTERN.match(ktru_code):
                continue

            # Извлекаем характеристики
            chars_list = []
            for sub in elem.iter():
                sub_tag = _strip_ns(sub.tag)
                if sub_tag in ("characteristic", "itemCharacteristic", "KTRUCharacteristic"):
                    c_name = _get_child_text(sub, "name")
                    # Значение может быть в value, values, qualityDescription
                    c_val = _get_child_text(sub, "value") or _get_child_text(sub, "values") or _get_child_text(sub, "qualityDescription")
                    if not c_val:
                        # Попробуем собрать дочерние теги
                        val_elem = _find_child(sub, "values") or _find_child(sub, "characteristics")
                        if val_elem is not None:
                            vals = [(v.text or "").strip() for v in val_elem if (v.text or "").strip()]
                            c_val = ", ".join(vals)
                    if c_name and c_val:
                        chars_list.append(f"{c_name}: {c_val}")
                    elif c_name:
                        chars_list.append(c_name)

            # Собираем raw_text как наименование + характеристики через '; '
            parts = []
            if name:
                parts.append(name)
            parts.extend(chars_list)
            raw_text = "; ".join(parts)

            builder.add_candidate(
                raw_text=raw_text,
                ktru_code=ktru_code,
                notice=notice_number,
                origin_ref=os.path.basename(xml_path),
            )


def process_xml_dir(xml_dir: str, builder: DatasetBuilder):
    """Рекурсивно обходит папку с XML-файлами и архивами ZIP."""
    if not os.path.exists(xml_dir):
        print(f"Ошибка: папка с XML '{xml_dir}' не найдена!", file=sys.stderr)
        sys.exit(1)

    xml_files = []
    if os.path.isfile(xml_dir):
        xml_files.append(xml_dir)
    else:
        for root_dir, _, files in os.walk(xml_dir):
            for file in files:
                if file.lower().endswith(".xml"):
                    xml_files.append(os.path.join(root_dir, file))
                elif file.lower().endswith(".zip"):
                    # Поддержка чтения XML прямо из ZIP-архивов
                    zip_path = os.path.join(root_dir, file)
                    try:
                        with zipfile.ZipFile(zip_path, "r") as z:
                            for zinfo in z.infolist():
                                if zinfo.filename.lower().endswith(".xml"):
                                    with z.open(zinfo) as zf:
                                        try:
                                            tree = ET.parse(zf)
                                            # Временная обработка дерева
                                            _parse_xml_file(f"{file}::{zinfo.filename}", builder)
                                        except Exception:
                                            pass
                    except Exception:
                        pass

    if not xml_files:
        print(f"Предупреждение: в '{xml_dir}' не найдено файлов .xml!", file=sys.stderr)
        return

    print(f"Найдено XML файлов: {len(xml_files)}. Обработка...")
    for idx, path in enumerate(xml_files, 1):
        _parse_xml_file(path, builder)
        if idx % 100 == 0:
            print(f"  Обработано {idx}/{len(xml_files)} XML-файлов...")


def main():
    parser = argparse.ArgumentParser(
        description="real_samples_tool — валидатор и генератор датасета real_samples.jsonl для B2B-РТС КТРУ"
    )
    subparsers = parser.add_subparsers(dest="mode", help="Режим работы: csv или xml")

    # Режим CSV
    csv_parser = subparsers.add_parser("csv", help="Сборка из CSV-файла (например, real_hunt_list.csv)")
    csv_parser.add_argument("input", help="Путь к файлу CSV")
    csv_parser.add_argument("--catalog", default=None, help="Путь к файлу catalog_full.jsonl / catalog_org.jsonl")
    csv_parser.add_argument("--out", default="real_samples.jsonl", help="Имя выходного файла JSONL (по умолч. real_samples.jsonl)")
    csv_parser.add_argument("--report", default="real_samples_report.txt", help="Имя файла отчёта (по умолч. real_samples_report.txt)")

    # Режим XML
    xml_parser = subparsers.add_parser("xml", help="Сборка из папки с XML-файлами извещений ЕИС")
    xml_parser.add_argument("input", help="Путь к папке с XML или файлу XML")
    xml_parser.add_argument("--catalog", default=None, help="Путь к файлу catalog_full.jsonl / catalog_org.jsonl")
    xml_parser.add_argument("--out", default="real_samples.jsonl", help="Имя выходного файла JSONL (по умолч. real_samples.jsonl)")
    xml_parser.add_argument("--report", default="real_samples_report.txt", help="Имя файла отчёта (по умолч. real_samples_report.txt)")

    args = parser.parse_args()

    if not args.mode:
        parser.print_help()
        sys.exit(1)

    print(f"Загрузка каталога КТРУ...")
    catalog, catalog_path = load_catalog(args.catalog)
    if catalog_path:
        print(f"  Каталог загружен из: {catalog_path} (активных позиций: {len(catalog)})")
    else:
        print("  [ВНИМАНИЕ] Каталог не найден. Проверка статуса (активен/архив) будет пропущена.")

    builder = DatasetBuilder(catalog=catalog, catalog_source=catalog_path)

    if args.mode == "csv":
        print(f"Обработка CSV: {args.input}")
        process_csv(args.input, builder)
    elif args.mode == "xml":
        print(f"Обработка XML: {args.input}")
        process_xml_dir(args.input, builder)

    builder.export_results(jsonl_path=args.out, report_path=args.report)


if __name__ == "__main__":
    main()
