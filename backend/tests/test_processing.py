"""Воркеры (backend/core/processing.py): идемпотентность пачек, отмена и удаление посреди работы, ретраи,
ошибки разбора, проверка ответа ML, watchdog. Без маркера db — то, что не требует базы.
"""

from __future__ import annotations

import io
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.core import models, processing, uploads
from backend.core.queue import CeleryQueue
from backend.core.settings import Settings
from backend.files import FileUnreadableError, MappingInvalidError
from backend.tasks import ml_worker
from contracts.ml_contracts import ClassificationResult, PipelineOptions, RawItem
from contracts.stub_pipeline import StubPipeline

from .conftest import HEADER, ROWS, Stack, xlsx_bytes

# --- Без базы -------------------------------------------------------------------------------------------------


def test_raw_columns_by_roles() -> None:
    mapping = {
        "columns": [
            {"index": 3, "role": "characteristics"},
            {"index": 0, "role": "name"},
            {"index": 1, "role": "characteristics"},
            {"index": 2, "role": "ignore"},
            {"index": 9, "role": "unit"},  # колонки нет в строке
        ]
    }
    assert processing.raw_columns(mapping, ["Стол", "120 см", "x", " дуб ", ""]) == {
        "name": "Стол",
        "characteristics": "120 см дуб",
    }


def test_status_and_band_agree_on_threshold(settings: Settings) -> None:
    edge = settings.model_copy(update={"green_threshold": 0.7, "yellow_threshold": 0.3})
    stored = processing.as_real(0.7)  # так уверенность 0.7 возвращается из колонки real
    assert stored != 0.7
    assert processing.band_of(stored, edge) == "green"
    assert processing.item_status(0.7, edge) == "processed"
    assert processing.band_of(0.69, edge) == "yellow"
    assert processing.band_of(0.29, edge) == "red"
    assert processing.item_status(0.5, edge) == "needs_review"


def test_file_error_hides_library_text() -> None:
    # backend/files оборачивает ошибку библиотеки через `from e` — её текст пользователю не показываем.
    wrapped = FileUnreadableError("Ошибка чтения Excel-файла: zip: bad magic number")
    wrapped.__cause__ = ValueError("zip: bad magic number")
    assert uploads.file_error(wrapped) == ("file_unreadable", uploads.UNREADABLE_MESSAGE, None)

    own = MappingInvalidError("Не выбрана колонка.", details={"error_code": "mapping_invalid", "reason": "name"})
    assert uploads.file_error(own) == ("mapping_invalid", "Не выбрана колонка.", {"reason": "name"})


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("C:\\fakepath\\Заявка.xlsx", "Заявка.xlsx"),
        ("../../etc/passwd.csv", "passwd.csv"),
        ("спец\x00ификация.pdf", "спецификация.pdf"),
        (None, ""),
        ("а" * 300 + ".xlsx", "а" * 250 + ".xlsx"),
    ],
)
def test_clean_filename(raw: str | None, clean: str) -> None:
    assert uploads.clean_filename(raw) == clean


def test_save_upload_is_atomic(tmp_path: Path) -> None:
    file_id = uuid.uuid4()
    stored = uploads.save_upload(io.BytesIO(b"abc"), tmp_path, file_id, "csv", max_bytes=3)
    assert (stored.storage_path, stored.size_bytes) == (f"{file_id}.csv", 3)
    with pytest.raises(uploads.UploadTooLarge):
        uploads.save_upload(io.BytesIO(b"abcd"), tmp_path, uuid.uuid4(), "csv", max_bytes=3)
    assert [p.name for p in tmp_path.iterdir()] == [f"{file_id}.csv"]


def test_upload_extensions_setting() -> None:
    assert Settings(upload_extensions=" XLSX, .xls,csv ").upload_extensions == ["xlsx", "xls", "csv"]
    with pytest.raises(ValueError, match="UPLOAD_EXTENSIONS"):
        Settings(upload_extensions="xlsx,docx")


class _RecordingCelery:
    def __init__(self) -> None:
        self.sent: list[tuple[str, list[Any], dict[str, Any]]] = []

    def send_task(self, name: str, args: list[Any], **options: Any) -> None:
        self.sent.append((name, args, options))


def test_celery_queue_sends_tasks_by_name() -> None:
    celery = _RecordingCelery()
    queue = CeleryQueue(celery)  # type: ignore[arg-type]
    queue.parse_file("j")
    queue.process_chunk("j", ["a", "b"])
    assert [(name, args) for name, args, _ in celery.sent] == [
        ("io.parse_file", ["j"]),
        ("ml.process_chunk", ["j", ["a", "b"]]),
    ]
    assert all(options["retry_policy"]["max_retries"] == 2 for _, _, options in celery.sent)


def test_process_chunk_task_retries_then_fails_items(monkeypatch: pytest.MonkeyPatch) -> None:
    failed: list[tuple[Any, ...]] = []
    monkeypatch.setattr(ml_worker, "worker_engine", lambda _: None)
    monkeypatch.setattr(ml_worker.processing, "fail_chunk", lambda *args: failed.append(args))
    job_id, item_id = str(uuid.uuid4()), str(uuid.uuid4())
    # Конвейера в этом процессе нет (модели не загружены) — как сбой ML.
    ml_worker.process_chunk.push_request(retries=0)
    try:
        with pytest.raises(RuntimeError):  # вне воркера retry() пробрасывает исходную ошибку
            ml_worker.process_chunk.run(job_id, [item_id])
    finally:
        ml_worker.process_chunk.pop_request()
    assert failed == []

    ml_worker.process_chunk.push_request(retries=len(ml_worker.RETRY_DELAYS_S))
    try:
        ml_worker.process_chunk.run(job_id, [item_id])
    finally:
        ml_worker.process_chunk.pop_request()
    assert failed == [(None, uuid.UUID(job_id), [uuid.UUID(item_id)], ml_worker.CHUNK_FAILED)]


# --- На базе --------------------------------------------------------------------------------------------------


def _job(stack: Stack, rows: list[list[Any]] | None = None) -> str:
    info = stack.upload(xlsx_bytes([HEADER, *(rows or ROWS)]))
    return stack.create_job(info["file_id"])["job_id"]


def _job_row(stack: Stack, job_id: str) -> models.Job:
    with Session(stack.engine) as session:
        return session.get(models.Job, uuid.UUID(job_id))


def _count(stack: Stack, table: Any) -> int:
    with Session(stack.engine) as session:
        return session.scalar(select(func.count()).select_from(table))


def _parse_only(stack: Stack, job_id: str) -> list[tuple[str, list[str]]]:
    """Только разбор: пачки остаются в очереди нетронутыми."""
    stack.queue.parse.remove(job_id)
    processing.parse_job(stack.engine, stack.settings, stack.queue, uuid.UUID(job_id))
    chunks, stack.queue.chunks = stack.queue.chunks, []
    return chunks


def _run(stack: Stack, chunk: tuple[str, list[str]], pipeline: Any = None) -> None:
    job_id, item_ids = chunk
    processing.process_chunk(
        stack.engine, stack.settings, pipeline or StubPipeline(), uuid.UUID(job_id), [uuid.UUID(i) for i in item_ids]
    )


@pytest.mark.db
def test_items_go_to_ml_in_chunks_of_50(stack: Stack) -> None:
    rows = [[f"Товар номер {n}", "", 1, "шт"] for n in range(120)]
    job_id = _job(stack, rows)
    chunks = _parse_only(stack, job_id)
    assert [len(ids) for _, ids in chunks] == [50, 50, 20]
    job = _job_row(stack, job_id)
    assert (job.status, job.total_items, job.started_at is not None) == ("processing", 120, True)
    with Session(stack.engine) as session:
        first = session.scalars(select(models.Item).order_by(models.Item.row_index).limit(1)).one()
    assert (first.raw_text, first.raw_row) == ("Товар номер 0", ["Товар номер 0", "", "1", "шт"])


@pytest.mark.db
def test_repeated_chunk_changes_nothing(stack: Stack) -> None:
    job_id = _job(stack)
    [chunk] = _parse_only(stack, job_id)
    _run(stack, chunk)
    predictions, job = _count(stack, models.Prediction), _job_row(stack, job_id)
    assert (job.status, job.processed_items) == ("done", 3)
    _run(stack, chunk)  # повторная доставка той же пачки
    assert _count(stack, models.Prediction) == predictions
    assert _job_row(stack, job_id).processed_items == 3


@pytest.mark.db
def test_cancel_and_delete_in_the_middle(stack: Stack) -> None:
    rows = [[f"Товар номер {n}", "", 1, "шт"] for n in range(60)]
    job_id = _job(stack, rows)
    first, second = _parse_only(stack, job_id)
    _run(stack, first)
    assert stack.client.post(f"/api/v1/jobs/{job_id}/cancel").json()["status"] == "cancelled"
    _run(stack, second)  # пачка после отмены пропускает работу
    job = _job_row(stack, job_id)
    assert (job.status, job.processed_items) == ("cancelled", 50)

    other = _job(stack)
    [chunk] = _parse_only(stack, other)
    assert stack.client.delete(f"/api/v1/jobs/{other}").status_code == 204
    _run(stack, chunk)  # задачи больше нет — воркер тихо завершается
    processing.fail_chunk(stack.engine, uuid.UUID(other), [uuid.uuid4()], "x")


@pytest.mark.db
def test_cancel_while_file_is_parsed(stack: Stack, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = _job(stack)
    real_parse_rows = processing.parse_rows

    def parse_and_cancel(*args: Any) -> Any:
        rows = real_parse_rows(*args)
        assert stack.client.post(f"/api/v1/jobs/{job_id}/cancel").status_code == 200
        return rows

    monkeypatch.setattr(processing, "parse_rows", parse_and_cancel)
    assert _parse_only(stack, job_id) == []
    assert (_job_row(stack, job_id).status, _count(stack, models.Item)) == ("cancelled", 0)


@pytest.mark.db
def test_parse_failures(stack: Stack) -> None:
    rows = [[f"Товар номер {n}", "", 1, "шт"] for n in range(5)]
    limited = stack.settings.model_copy(update={"max_items_per_job": 3})
    job_id = _job(stack, rows)
    stack.drain(settings=limited)
    job = _job_row(stack, job_id)
    assert (job.status, job.error_code) == ("failed", "limit_exceeded")
    assert job.error == "Позиций в файле: 5, а в одной задаче — не больше 3. Разбейте файл на части."

    lost = _job(stack)
    for path in stack.stored_files():
        path.unlink()
    stack.drain()
    job = _job_row(stack, lost)
    assert (job.status, job.error_code, job.finished_at is not None) == ("failed", "internal_error", True)


@pytest.mark.db
def test_all_items_failed_means_failed_job(stack: Stack) -> None:
    job = stack.finished_job([["Сбой __fail__ один", "", 1, "шт"], ["Сбой __fail__ два", "", 1, "шт"]])
    assert (job["status"], job["failed_items"], job["error_code"]) == ("failed", 2, "internal_error")
    assert job["error"] == processing.NOTHING_PROCESSED


@pytest.mark.db
def test_fail_chunk_after_last_retry(stack: Stack) -> None:
    job_id = _job(stack)
    [(_, item_ids)] = _parse_only(stack, job_id)
    processing.fail_chunk(stack.engine, uuid.UUID(job_id), [uuid.UUID(i) for i in item_ids], "Сбой.")
    job = _job_row(stack, job_id)
    assert (job.status, job.failed_items, job.processed_items) == ("failed", 3, 0)
    items = stack.client.get(f"/api/v1/jobs/{job_id}/items").json()["items"]
    assert {(i["status"], i["error"]) for i in items} == {("failed", "Сбой.")}


class _EditingPipeline:
    """Заглушка, ответы которой можно испортить: проверка ответа ML (check_results) и режим degraded."""

    def __init__(self, edit: Any):
        self.stub = StubPipeline()
        self.edit = edit

    def is_ready(self) -> dict[str, bool]:
        return self.stub.is_ready()

    def classify_batch(
        self, items: list[RawItem], catalog_version_id: str, options: PipelineOptions | None = None
    ) -> list[ClassificationResult]:
        return [self.edit(r) for r in self.stub.classify_batch(items, catalog_version_id, options)]


@pytest.mark.db
def test_invalid_ml_answer_fails_only_its_item(stack: Stack) -> None:
    def break_first(result: ClassificationResult) -> ClassificationResult:
        if not result.extracted or not result.extracted.name.startswith("Ноутбук"):
            return result
        bad = result.predictions[0].model_copy(update={"ktru_code": "00.00.00.000-99999999"})
        return result.model_copy(update={"predictions": [bad, *result.predictions[1:]]})

    job = stack.finished_job(ROWS, pipeline=_EditingPipeline(break_first))
    assert (job["status"], job["processed_items"], job["failed_items"]) == ("done_with_errors", 2, 1)
    items = stack.client.get(f"/api/v1/jobs/{job['job_id']}/items", params={"status": "failed"}).json()["items"]
    assert items[0]["error"].startswith("Некорректный ответ ML: кода 00.00.00.000-99999999 нет в версии каталога")


@pytest.mark.db
def test_degraded_mode_and_archived_code(stack: Stack) -> None:
    with Session(stack.engine) as session, session.begin():
        session.execute(update(models.CatalogItem).values(status="archived"))
    job = stack.finished_job(ROWS, pipeline=_EditingPipeline(lambda r: r.model_copy(update={"mode": "degraded"})))
    assert job["effective_mode"] == "degraded"
    items = stack.client.get(f"/api/v1/jobs/{job['job_id']}/items", params={"flag": "archived_code"}).json()
    assert items["total"] == 3


@pytest.mark.db
def test_watchdog_requeues_stale_jobs(stack: Stack) -> None:
    queued = _job(stack)
    stack.queue.parse.clear()  # сообщение о разборе потеряно
    processing_job = _job(stack, [[f"Товар номер {n}", "", 1, "шт"] for n in range(60)])
    first, _lost = _parse_only(stack, processing_job)
    _run(stack, first)

    assert processing.requeue_stale_jobs(stack.engine, stack.queue, stale_after=timedelta(0)) == 2
    assert stack.queue.parse == [queued]
    [(job_id, item_ids)] = stack.queue.chunks
    assert (job_id, len(item_ids)) == (processing_job, 10)  # только pending-позиции

    # Только что поднятые задачи watchdog не трогает, пока они снова не простоят stale_after.
    assert processing.requeue_stale_jobs(stack.engine, stack.queue, stale_after=timedelta(minutes=5)) == 0
    stack.drain()
    assert {_job_row(stack, j).status for j in (queued, processing_job)} == {"done"}
    assert processing.requeue_stale_jobs(stack.engine, stack.queue, stale_after=timedelta(0)) == 0
