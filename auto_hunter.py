#!/usr/bin/env python3
"""
auto_hunter.py — автоматический сбор реальных строк закупок по 44-ФЗ с zakupki.gov.ru
на основе кодов КТРУ из таблицы real_hunt_list.csv.

Скрипт:
1. Загружает каталог КТРУ (catalog_org.jsonl).
2. Берёт проверенные коды КТРУ из real_hunt_list.csv по 5 сферам (канцелярия, мебель, оргтехника, продукты, медицина).
3. Находит реальные извещения по 44-ФЗ в ЕИС для каждого кода.
4. Загружает печатные формы извещений и извлекает объекты закупки с характеристиками заказчика.
5. Проверяет позиции через DatasetBuilder (активный статус в каталоге, не шаблон, длина >= 15, лимит 5 на код).
6. Скачивает реальные файлы спецификаций в tests/fixtures/real_specs/ для демонстрации.
7. Экспортирует итоговый real_samples.jsonl, real_samples_report.txt и обновляет real_hunt_list.csv.
"""

import csv
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from typing import Dict, List, Set, Tuple

from real_samples_tool import DatasetBuilder, load_catalog

CTX = ssl._create_unverified_context()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}


def clean_html(text: str) -> str:
    """Удаляет теги и нормализует пробелы."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


def fetch_url(url: str, retries: int = 2, timeout: int = 12) -> str:
    """Загружает страницу с повторами при сбоях."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, context=CTX, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except Exception:
            if attempt == retries - 1:
                return ""
            time.sleep(0.5)
    return ""


def search_notices_by_code(ktru_code: str, max_notices: int = 4) -> List[str]:
    """Ищет номера извещений по коду КТРУ в ЕИС."""
    encoded_code = urllib.parse.quote(ktru_code)
    url = f"https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString={encoded_code}&morphology=on&fz44=on&recordsPerPage=_10&sortBy=UPDATE_DATE"
    html = fetch_url(url)
    if not html:
        return []
    numbers = re.findall(r"\b(\d{19})\b", html)
    unique_numbers = []
    for n in numbers:
        if n not in unique_numbers:
            unique_numbers.append(n)
        if len(unique_numbers) >= max_notices:
            break
    return unique_numbers


def parse_print_form(reg_num: str) -> List[Tuple[str, str]]:
    """
    Разбирает печатную форму извещения (printForm/view.html).
    Возвращает список (ktru_code, raw_text).
    """
    url = f"https://zakupki.gov.ru/epz/order/notice/printForm/view.html?regNumber={reg_num}"
    html = fetch_url(url)
    if not html:
        return []

    blocks = html.split("Характеристики товара, работы, услуги")
    if len(blocks) <= 1:
        # Альтернативный разбор: поиск строк таблицы товаров
        raw_matches = re.findall(r"(\d{2}\.\d{2}\.\d{2}\.\d{3}-\d{8})\s+([^<]+)", html)
        res = []
        for code, name in raw_matches:
            c_name = clean_html(name)
            if len(c_name) >= 15:
                res.append((code, c_name))
        return res

    parsed_items = []
    for b in blocks[1:]:
        m = re.search(r"(\d{2}\.\d{2}\.\d{2}\.\d{3}-\d{8})\s*([^)<]+)", b[:400])
        if not m:
            continue
        code = m.group(1).strip()
        name = clean_html(m.group(2))

        rows = re.findall(r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>", b, re.DOTALL)
        chars = []
        for c_name_raw, c_val_raw in rows:
            c_name = clean_html(c_name_raw)
            c_val = clean_html(c_val_raw)
            if not c_val or c_name in ("Наименование характеристики", "№ п/п", "№", "Инструкция по заполнению характеристики в заявке"):
                continue
            if "соответствии с КТРУ" in c_name or "Характеристика №" in c_name:
                chars.append(c_val)
            else:
                chars.append(f"{c_name}: {c_val}")

        chars_str = "; ".join(chars)
        full_text = f"{name}; {chars_str}" if chars_str else name
        parsed_items.append((code, full_text))

    return parsed_items


def download_sample_docs(reg_nums: List[str], dest_dir: str, max_files: int = 6):
    """Скачивает реальные файлы спецификаций из раздела документов извещений."""
    os.makedirs(dest_dir, exist_ok=True)
    manifest = []
    downloaded = 0

    print(f"\nПоиск и загрузка реальных файлов спецификаций в {dest_dir}...")
    for reg_num in reg_nums:
        if downloaded >= max_files:
            break
        url = f"https://zakupki.gov.ru/epz/order/notice/ea20/view/documents.html?regNumber={reg_num}"
        html = fetch_url(url)
        if not html:
            continue

        matches = re.findall(r'href=[\"\']([^\"\']*(?:download|fileStore|file)[^\"\']*)[\"\'][^>]*>([^<]+)<', html)
        for link, title in matches:
            t = title.strip()
            ext = None
            for e in (".xlsx", ".xls", ".docx", ".pdf"):
                if e in t.lower() or e in link.lower():
                    ext = e
                    break
            if not ext and ("спецификац" in t.lower() or "описание" in t.lower() or "тз" in t.lower()):
                ext = ".pdf"

            if ext:
                full_link = link if link.startswith("http") else f"https://zakupki.gov.ru{link}"
                clean_name = re.sub(r'[\\/*?:"<>|]', "_", t)
                if not clean_name.endswith(ext):
                    clean_name += ext
                filename = f"{reg_num}_{clean_name}"
                save_path = os.path.join(dest_dir, filename)
                try:
                    req = urllib.request.Request(full_link, headers=HEADERS)
                    with urllib.request.urlopen(req, context=CTX, timeout=15) as resp:
                        content = resp.read()
                        if len(content) > 1024:
                            with open(save_path, "wb") as f:
                                f.write(content)
                            manifest.append(f"- **{filename}** ({len(content)//1024} КБ)\n  Извещение: `{reg_num}` | Название: *{t}*")
                            downloaded += 1
                            print(f"  [+] Скачан файл демо: {filename} ({len(content)//1024} КБ)")
                            if downloaded >= max_files:
                                break
                except Exception:
                    pass

    readme_path = os.path.join(dest_dir, "README.md")
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write("# Реальные файлы спецификаций из извещений ЕИС (44-ФЗ)\n\n")
        f.write("Файлы скачаны из вложений к реальным извещениям по 44-ФЗ за 2025–2026 годы для демонстрации разбора файлов.\n\n")
        f.write("\n".join(manifest) + "\n")


def load_codes_by_category(hunt_path: str) -> Dict[str, List[Tuple[str, str]]]:
    """Группирует коды КТРУ из таблицы real_hunt_list.csv по категориям."""
    by_cat = defaultdict(list)
    if not os.path.isfile(hunt_path):
        return by_cat
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            with open(hunt_path, "r", encoding=enc) as f:
                reader = csv.reader(f, delimiter=";")
                header = next(reader)
                for row in reader:
                    if row and len(row) > 2:
                        area = row[0].strip().lower()
                        code = row[1].strip()
                        name = row[2].strip()
                        if area and code:
                            by_cat[area].append((code, name))
                break
        except UnicodeDecodeError:
            continue
    return by_cat


def update_hunt_list_csv(hunt_path: str, accepted_items: List[Dict]):
    """Заполняет колонки notice и raw_text в real_hunt_list.csv найденными данными."""
    if not os.path.isfile(hunt_path):
        return
    # Словарь code -> (notice, raw_text)
    mapping = {}
    for item in accepted_items:
        code = item["expected_ktru_code"]
        if code not in mapping:
            # Извлекаем 19 цифр из "извещение XXXXXXXXXXXXXXXXXXX"
            m = re.search(r"\d{19}", item["source"])
            notice_val = m.group(0) if m else item["source"]
            mapping[code] = (notice_val, item["raw_text"])

    lines_out = []
    with open(hunt_path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)
        lines_out.append(";".join(header))
        for row in reader:
            if row and len(row) > 1:
                code = row[1].strip()
                if code in mapping and (len(row) <= 6 or not row[6].strip()):
                    notice_val, raw_val = mapping[code]
                    while len(row) < 8:
                        row.append("")
                    row[5] = notice_val
                    row[6] = raw_val.replace(";", ",")  # экранируем внутренние точки с запятой
                    row[7] = "да"
                lines_out.append(";".join(row))

    with open(hunt_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines_out) + "\n")
    print(f"Таблица {hunt_path} обновлена найденными позициями!")


def main():
    print("=" * 60)
    print(" АВТОМАТИЧЕСКИЙ СБОР РЕАЛЬНЫХ СТРОК ЗАКУПОК ИЗ ЕИС (44-ФЗ)")
    print("=" * 60)

    # 1. Загрузка каталога КТРУ
    catalog, cat_path = load_catalog("catalog_org.jsonl")
    if cat_path:
        print(f"Каталог КТРУ: {cat_path} (активных позиций: {len(catalog)})")
    else:
        print("[ВНИМАНИЕ] Каталог не найден. Проверка статуса пропущена.")

    builder = DatasetBuilder(catalog=catalog, catalog_source=cat_path)

    # 2. Загрузка кодов по категориям
    hunt_path = "C:/Users/molos/Downloads/Telegram Desktop/real_hunt_list.csv"
    if not os.path.exists(hunt_path):
        hunt_path = "real_hunt_list.csv"
    by_cat = load_codes_by_category(hunt_path)
    print(f"Загружено кодов из {hunt_path}: {sum(len(v) for v in by_cat.values())} по {len(by_cat)} категориям")

    # Порядок категорий для разнообразия датасета
    categories_order = ["канцелярия", "мебель", "оргтехника", "продукты", "медицина"]
    all_notices_collected: List[str] = []
    seen_notices: Set[str] = set()

    TARGET_TOTAL = 150  # Цель: 150-200 качественных строк

    for cat in categories_order:
        items = by_cat.get(cat, [])
        print(f"\n>>> Сбор по категории '{cat}' (доступно кодов КТРУ: {len(items)})...")
        cat_count_start = len([a for a in builder.accepted if a.get("_area") == cat])

        for code, name in items[:15]:  # берём до 15 кодов в каждой категории
            if len([a for a in builder.accepted if a.get("_area") == cat]) - cat_count_start >= 35:
                # Достаточно для одной категории (чтобы было равномерное распределение)
                break
            if len(builder.accepted) >= TARGET_TOTAL:
                break

            print(f"  Поиск извещений по коду {code} ({name[:30]})...")
            notices = search_notices_by_code(code, max_notices=3)
            time.sleep(0.3)

            for reg_num in notices:
                if reg_num in seen_notices:
                    continue
                seen_notices.add(reg_num)
                all_notices_collected.append(reg_num)

                parsed_items = parse_print_form(reg_num)
                if not parsed_items:
                    continue

                added_in_notice = 0
                for item_code, item_text in parsed_items:
                    # Добавляем в датасет
                    ok = builder.add_candidate(
                        raw_text=item_text,
                        ktru_code=item_code,
                        notice=reg_num,
                        origin_ref=f"ЕИС {reg_num}",
                        area=cat,
                    )
                    if ok:
                        added_in_notice += 1

                if added_in_notice > 0:
                    print(f"    [+] Извещение {reg_num}: добавлено {added_in_notice} позиций (всего в датасете: {len(builder.accepted)})")

        time.sleep(0.5)

    # 3. Экспорт результатов
    out_jsonl = "real_samples.jsonl"
    out_report = "real_samples_report.txt"
    builder.export_results(jsonl_path=out_jsonl, report_path=out_report)

    # 4. Обновление CSV таблицы
    update_hunt_list_csv(hunt_path, builder.accepted)

    # 5. Скачивание демо-файлов спецификаций
    sample_dir = os.path.join("tests", "fixtures", "real_specs")
    download_sample_docs(all_notices_collected, dest_dir=sample_dir, max_files=6)

    print("\n" + "=" * 60)
    print(f" [УСПЕХ] Собрано {len(builder.accepted)} реальных строк закупок!")
    print(f" Файл для Jove: {os.path.abspath(out_jsonl)}")
    print(f" Файлы спецификаций: {os.path.abspath(sample_dir)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
