"""End-to-End тест дополнительных задач финала (Задачи A и B).

Сценарий:
1. Загрузка комплекта из 3 файлов (XLSX, CSV):
   - Определение структуры и проверка флага уверенности ролей колонок (Sheet.mapping_confident).
2. Разбор строк каждого файла (parse_rows):
   - Сохранение raw_text и исходных строк для каждого файла-источника.
3. Классификация через ML-конвейер (StubPipeline):
   - Получение предсказаний и характеристик КТРУ.
   - Имитация незаполненной обязательной характеристики (status='missing', required=True).
4. Ручное заполнение обязательной характеристики пользователем:
   - Установка value и смена статуса на 'filled' (источник 'пользователь').
   - Проверка уменьшения счётчика missing_required_count.
5. Экспорт результатов:
   - Пакетная выгрузка комплекта в XLSX, CSV, JSON и APPLICATION (export_batch_results):
     * Проверка первой колонки «Файл-источник».
     * Проверка колонки «Исходный текст».
     * Проверка запрета формата 'source'.
     * Проверка попадания ручных правок со статусом 'пользователь'.
   - Выгрузка задачи «для заявки» в XLSX (export_job_results / export_application_xlsx):
     * Проверка 9 стандартных колонок закупочной документации.
     * Проверка защиты от Formula Injection (cell.data_type = 's').
     * Проверка корректности счётчиков X-Unreviewed-Count и X-Missing-Required-Count.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from uuid import uuid4

import openpyxl
import pytest

from backend.files import (
    ColumnMappingItem,
    ExportFormat,
    ExportItem,
    ItemCharacteristicExport,
    export_batch_results,
    export_job_results,
    parse_rows,
    read_file,
)
from backend.files.exceptions import UnsupportedTypeError
from contracts.ml_contracts import RawItem
from contracts.stub_pipeline import StubPipeline


@pytest.fixture
def sample_batch_files(tmp_path: Path) -> list[Path]:
    """Создаёт 3 файла для тестирования загрузки и обработки комплекта."""
    # Файл 1: XLSX (Ноутбуки)
    file1 = tmp_path / "spec_laptops.xlsx"
    wb1 = openpyxl.Workbook()
    ws1 = wb1.active
    ws1.title = "Спецификация"
    ws1.append(["№ п/п", "Наименование товара", "Характеристики", "Кол-во", "Ед. изм."])
    ws1.append(["1", "Ноутбук Lenovo ThinkPad L15", '15.6", Core i5, 16 ГБ', "5", "шт"])
    ws1.append(["2", "Ноутбук ASUS Vivobook 15", '15.6", Ryzen 5, 8 ГБ', "3", "шт"])
    wb1.save(file1)

    # Файл 2: XLSX (Принтеры)
    file2 = tmp_path / "spec_printers.xlsx"
    wb2 = openpyxl.Workbook()
    ws2 = wb2.active
    ws2.title = "Оборудование"
    ws2.append(["№", "Наименование", "Количество", "Единица"])
    ws2.append(["1", "Принтер лазерный Xerox B230", "2", "шт"])
    wb2.save(file2)

    # Файл 3: CSV (Расходные материалы)
    file3 = tmp_path / "spec_supplies.csv"
    with open(file3, "w", encoding="utf-8-sig", newline="") as f:
        f.write("Наименование товара;Кол-во;Ед. изм.\n")
        f.write("Бумага офисная SvetoCopy А4 80г/м2;50;пачка\n")

    return [file1, file2, file3]


def test_extras_e2e_full_workflow(sample_batch_files: list[Path]):
    # -------------------------------------------------------------------------
    # ШАГ 1: Загрузка и проверка mapping_confident для комплекта файлов
    # -------------------------------------------------------------------------
    pipeline = StubPipeline()
    all_batch_items: list[ExportItem] = []

    for file_path in sample_batch_files:
        info = read_file(file_path)
        assert len(info.sheets) == 1
        sheet = info.sheets[0]
        # Все 3 файла имеют стандартные заголовки -> mapping_confident обязан быть True
        assert sheet.mapping_confident is True, (
            f"Ожидалась уверенность для {file_path.name}"
        )

        # Сопоставление колонок
        mapping = [
            ColumnMappingItem(index=col.index, role=col.suggested_role)
            for col in sheet.columns
        ]

        # ---------------------------------------------------------------------
        # ШАГ 2: Разбор строк файла
        # ---------------------------------------------------------------------
        parsed_rows = parse_rows(
            file_path,
            sheet_name=sheet.name,
            header_row=sheet.header_row,
            column_mapping=mapping,
        )
        assert len(parsed_rows) >= 1

        # ---------------------------------------------------------------------
        # ШАГ 3: Классификация через ML (StubPipeline)
        # ---------------------------------------------------------------------
        raw_items = [
            RawItem(
                item_id=str(uuid4()),
                text=p.raw_text,
                columns={"name": p.raw_row[1] if len(p.raw_row) > 1 else p.raw_text},
            )
            for p in parsed_rows
        ]
        results = pipeline.classify_batch(raw_items, catalog_version_id="2026-10-25")
        assert len(results) == len(parsed_rows)

        # Формируем ExportItem для каждой позиции
        for p, res in zip(parsed_rows, results):
            top1 = res.predictions[0] if res.predictions else None
            ktru_code = top1.ktru_code if top1 else None
            ktru_name = top1.name if top1 else None
            conf = top1.confidence if top1 else None

            item = ExportItem(
                source_filename=file_path.name,
                row_index=p.row_index,
                raw_row=p.raw_row,
                raw_text=p.raw_text,
                raw_name=p.raw_row[1] if len(p.raw_row) > 1 else p.raw_text,
                unit="шт"
                if "шт" in p.raw_row
                else ("пачка" if "пачка" in p.raw_row else None),
                ktru_code=ktru_code,
                ktru_name=ktru_name,
                confidence=conf,
                status="processed",
                source="prediction",
                characteristics=[],
            )
            all_batch_items.append(item)

    # Всего должно быть 2 (ноутбуки) + 1 (принтер) + 1 (бумага) = 4 позиции
    assert len(all_batch_items) == 4

    # -------------------------------------------------------------------------
    # ШАГ 4: Назначение характеристик и моделирование незаполненной обязательной
    # -------------------------------------------------------------------------
    # Первая позиция: Ноутбук Lenovo
    laptop_item = all_batch_items[0]
    laptop_item.characteristics = [
        ItemCharacteristicExport(
            name="Тип процессора",
            required=True,
            value=None,
            found="Intel Core i5",
            status="match",
        ),
        ItemCharacteristicExport(
            name="Объем оперативной памяти",
            required=True,
            value=None,
            found=None,  # Не найдено в тексте!
            status="missing",
        ),
        ItemCharacteristicExport(
            name="Диагональ экрана",
            required=False,
            value=None,
            found="15.6 дюймов",
            status="match",
        ),
    ]

    # Проверяем: у ноутбука есть 1 незаполненная обязательная характеристика
    res_before_edit = export_batch_results(
        all_batch_items, export_format=ExportFormat.XLSX
    )
    assert res_before_edit.missing_required_count == 1
    assert res_before_edit.unreviewed_count == 4

    # Пользователь заполняет недостающую характеристику вручную:
    missing_char = next(
        c for c in laptop_item.characteristics if c.name == "Объем оперативной памяти"
    )
    missing_char.value = "16 ГБ"
    missing_char.status = "filled"
    laptop_item.status = "confirmed"
    laptop_item.source = "user"

    # Теперь все обязательные характеристики заполнены!
    res_after_edit = export_batch_results(
        all_batch_items, export_format=ExportFormat.XLSX
    )
    assert res_after_edit.missing_required_count == 0
    assert res_after_edit.unreviewed_count == 3  # 1 позиция подтверждена

    # -------------------------------------------------------------------------
    # ШАГ 5: Экспорт комплекта (Задача A) во всех форматах
    # -------------------------------------------------------------------------
    # 5.1 Выгрузка комплекта в XLSX
    wb_batch = openpyxl.load_workbook(io.BytesIO(res_after_edit.content))
    ws_batch = wb_batch.active
    rows_batch = [[cell.value for cell in row] for row in ws_batch.iter_rows()]

    # Первая колонка - "Файл-источник", вторая - "Исходный текст"
    assert rows_batch[0][0] == "Файл-источник"
    assert rows_batch[0][1] == "Исходный текст"
    assert rows_batch[0][2] == "Код КТРУ"
    assert rows_batch[1][0] == "spec_laptops.xlsx"
    assert rows_batch[3][0] == "spec_printers.xlsx"
    assert rows_batch[4][0] == "spec_supplies.csv"

    # 5.2 Выгрузка комплекта в CSV
    res_csv = export_batch_results(all_batch_items, export_format=ExportFormat.CSV)
    csv_text = res_csv.content.decode("utf-8-sig")
    csv_lines = csv_text.strip().splitlines()
    assert csv_lines[0].startswith("Файл-источник;Исходный текст")
    assert "spec_laptops.xlsx" in csv_lines[1]
    assert "spec_supplies.csv" in csv_lines[-1]

    # 5.3 Выгрузка комплекта в JSON
    res_json = export_batch_results(all_batch_items, export_format=ExportFormat.JSON)
    json_data = json.loads(res_json.content.decode("utf-8"))
    assert len(json_data) == 4
    assert json_data[0]["source_filename"] == "spec_laptops.xlsx"
    assert json_data[0]["status"] == "confirmed"

    # 5.4 Выгрузка комплекта в формате APPLICATION («для заявки»)
    res_batch_app = export_batch_results(
        all_batch_items, export_format=ExportFormat.APPLICATION
    )
    wb_batch_app = openpyxl.load_workbook(io.BytesIO(res_batch_app.content))
    ws_batch_app = wb_batch_app.active
    app_rows = [[cell.value for cell in row] for row in ws_batch_app.iter_rows()]

    # Заголовок batch application содержит Файл-источник + 9 колонок заявки
    assert app_rows[0][0] == "Файл-источник"
    assert app_rows[0][1] == "№ позиции"
    assert app_rows[0][2] == "Исходное наименование"
    assert app_rows[0][5] == "Характеристика"
    assert app_rows[0][6] == "Значение"
    assert app_rows[0][7] == "Единица измерения"
    assert app_rows[0][8] == "Обязательная"
    assert app_rows[0][9] == "Источник значения"

    # Проверяем строку с заполненной пользователем характеристикой
    ram_row = next(r for r in app_rows if r[5] == "Объем оперативной памяти")
    assert ram_row[0] == "spec_laptops.xlsx"
    assert ram_row[6] == "16 ГБ"
    assert ram_row[8] == "да"
    assert ram_row[9] == "пользователь"

    # 5.5 Проверка запрета формата source для комплекта
    with pytest.raises(UnsupportedTypeError) as exc:
        export_batch_results(all_batch_items, export_format="source")
    assert "не поддерживается для выгрузки комплекта" in str(exc.value)

    # -------------------------------------------------------------------------
    # ШАГ 6: Выгрузка отдельной задачи в формате APPLICATION (Задача B)
    # -------------------------------------------------------------------------
    laptop_items_only = [all_batch_items[0], all_batch_items[1]]
    res_single_app = export_job_results(
        laptop_items_only,
        export_format=ExportFormat.APPLICATION,
        original_filename="spec_laptops.xlsx",
    )
    assert res_single_app.filename == "spec_laptops_application.xlsx"
    wb_single_app = openpyxl.load_workbook(io.BytesIO(res_single_app.content))
    ws_single_app = wb_single_app.active
    single_app_rows = [
        [cell.value for cell in row] for row in ws_single_app.iter_rows()
    ]

    # У отдельной задачи 9 колонок (без Файл-источник)
    assert single_app_rows[0] == [
        "№ позиции",
        "Исходное наименование",
        "Код КТРУ",
        "Наименование КТРУ",
        "Характеристика",
        "Значение",
        "Единица измерения",
        "Обязательная",
        "Источник значения",
    ]
    # Все ячейки строковые (защита от формульных инъекций)
    for row in ws_single_app.iter_rows():
        for cell in row:
            assert cell.data_type in ("s", "inlineStr")
