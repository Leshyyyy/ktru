"""watchdog (FR-2.5): beat запускает его раз в минуту в очереди io."""

from __future__ import annotations

import logging

from .celery_app import app

log = logging.getLogger(__name__)


@app.task(name="io.watchdog", ignore_result=True)
def watchdog() -> None:
    """Задачи в queued, parsing или processing без обновлений дольше 5 минут → заново в очередь их pending-позиции.

    Логика появится вместе с parse_file и process_chunk (план на 29.10), пока задач нет — проверять нечего.
    """
    log.debug("watchdog: задач обработки пока нет")
