# Контракт модуля разбора файлов и экспорта

Владелец: Донир.  
Потребители: Данил (`backend.app.routes.files`, `backend.tasks.jobs`, `backend.app.routes.export`), Таир (схемы данных).

Пакет `backend.files` отвечает за полный цикл работы со спецификациями: чтение файлов произвольных форматов (XLSX, XLS, CSV, PDF), автосопоставление колонок, разбор в строки для Celery-задачи и безопасный экспорт результатов с экранированием формул.

## 1. Интерфейс функций

### Быстрый предпросмотр (`POST /files`)

```python
def read_file(
    path: Union[str, Path],
    original_filename: Optional[str] = None,
    file_id: Optional[UUID] = None,
) -> FileInfo
```
- **Скорость:** отвечает менее чем за 2 секунды даже для больших файлов (для PDF разбираются только первые 3 страницы, `row_count` равен `null`).
- **Форматы:** XLSX (на движке `python-calamine`), XLS, CSV (автоопределение кодировок UTF-8, UTF-8 BOM, Windows-1251 и разделителей `;`, `,`, `\t`), PDF (`pdfplumber`).
- **Результат:** `FileInfo` с метаданными листов (`Sheet`), списками колонок (`ColumnInfo`), первыми 10 строками предпросмотра (`preview`) и предложенными ролями колонок (`suggested_role`).

### Автоопределение ролей колонок

```python
def suggest_roles(
    headers: List[str],
    preview_rows: List[List[str]] = None,
) -> List[ColumnMappingItem]
```
- Определяет роли по словарю ключевых слов заголовков (с нечётким сравнением) и эвристикам по содержимому ячеек первых строк.
- Роли: `name` (наименование, строго одна), `description`, `characteristics`, `quantity` (не более одной), `unit` (не более одной), `ignore`.

### Хеширование для дедупликации и подсказок раскладки

```python
def compute_file_sha256(path: Union[str, Path]) -> str
def compute_headers_hash(headers: List[str]) -> str
```
- `compute_file_sha256`: вычисление SHA-256 файла для проверки повторной загрузки (`duplicate_of_job_id`).
- `compute_headers_hash`: нормализованный устойчивый хеш заголовков колонок листа для подстановки ранее подтверждённого сопоставления (`layout_hint`).

### Полный разбор строк (Celery-задача `parse_file`)

```python
def parse_rows(
    path: Union[str, Path],
    sheet_name: Optional[str] = None,
    header_row: int = 0,
    column_mapping: Union[List[ColumnMappingItem], List[Dict[str, Any]]] = None,
) -> List[ParsedRow]
```
- Вызывается воркером при старте задачи обработки.
- **Очистка данных:** пропускает пустые строки, строки «Итого» / «Всего», повторяющиеся шапки таблиц на страницах PDF.
- **Сохранение индексов:** `row_index` сохраняет исходный номер строки относительно заголовка (пропуски не сбивают нумерацию).
- **Склейка `raw_text`:** значения колонок `name`, `description`, `characteristics` склеиваются через пробел слева направо.
- **Сохранение `raw_row`:** исходная строка целиком сохраняется в строковом виде для последующего экспорта.
- **Лимиты:** максимум 1000 строк в задаче (`LimitExceededError`), максимум 5000 строк в файле.

### Экспорт результатов (`GET /jobs/{id}/export`)

```python
def export_job_results(
    items: List[Union[ExportItem, Dict[str, Any]]],
    headers: Optional[List[str]] = None,
    export_format: Union[str, ExportFormat] = ExportFormat.SOURCE,
    original_filename: str = "specification.xlsx",
    source_path: Optional[Union[str, Path]] = None,
    sheet_name: Optional[str] = None,
    allow_unreviewed: bool = True,
    csv_delimiter: str = ";",
    header_row: Optional[int] = None,
) -> ExportResult
```
- **Форматы:** `source` (формат исходного файла, для PDF — XLSX), `xlsx`, `csv` (UTF-8-SIG с разделителем `;`), `json`.
- **Добавленные колонки (6 штук):**
  1. `Код КТРУ`
  2. `Наименование КТРУ`
  3. `Уверенность (%)`
  4. `Статус` (Подтверждено, Исправлено, Не проверено, Требует проверки, Ошибка, Не обработана)
  5. `Источник` (Модель, Пользователь)
  6. `Предупреждения`
- **Безопасность (Formula Injection):**
  - В CSV ячейки со знаками `=`, `+`, `-`, `@` экранируются ведущим апострофом `'`.
  - В XLSX все значения записываются ячейками строкового типа (`cell.data_type = 's'`).
- **Бизнес-правила:**
  - `allow_unreviewed=False` при наличии непроверенных строк генерирует `UnreviewedItemsError` (HTTP 409).
  - Заголовок `X-Unreviewed-Count` возвращает точное число непроверенных позиций.
  - Заголовок `Content-Disposition` формируется по RFC 5987 / RFC 6266 с безопасным ASCII fallback для совместимости со Starlette/ASGI.

## 2. Исключения

| Исключение | HTTP-код | Бизнес-код ошибки | Когда возникает |
| --- | --- | --- | --- |
| `FileUnreadableError` | 422 | `file_unreadable` | Повреждённый файл, пустой файл, отсутствие данных |
| `UnsupportedTypeError` | 415 | `unsupported_file_type` | Расширение файла не входит в список разрешённых |
| `FileTooLargeError` | 413 | `file_too_large` | Размер файла превышает установленный лимит |
| `MappingInvalidError` | 422 | `mapping_invalid` | Нет колонки `name`, дубли ролей `name`/`quantity`/`unit` |
| `LimitExceededError` | 422 | `limit_exceeded` | Более 1000 позиций в задаче или более 5000 строк в файле |
| `UnreviewedItemsError` | 409 | `unreviewed_items` | Выгрузка при `allow_unreviewed=False` с непроверенными строками |
