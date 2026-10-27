"""Настройки Celery и сигнал готовности ML-воркера."""

from __future__ import annotations

import json
import time

from backend.core.readiness import READY_KEY, Heartbeat, read_status, write_status
from backend.tasks.celery_app import app
from backend.tasks.ml_worker import _payload

from .conftest import FakeRedis


def test_celery_is_configured_for_reliable_json_tasks() -> None:
    conf = app.conf
    assert conf.accept_content == ["json"]
    assert conf.task_serializer == "json"
    assert conf.task_acks_late is True
    assert conf.worker_prefetch_multiplier == 1
    assert (conf.task_soft_time_limit, conf.task_time_limit) == (120, 180)
    assert {q.name for q in conf.task_queues} == {"io", "ml"}
    assert conf.task_default_queue == "io"
    assert app.amqp.router.route({}, "ml.process_chunk")["queue"].name == "ml"
    assert app.amqp.router.route({}, "io.parse_file")["queue"].name == "io"
    assert conf.beat_schedule["watchdog"]["task"] == "io.watchdog"


def test_ml_worker_waits_for_model_loading() -> None:
    assert app.conf.worker_proc_alive_timeout == 600


def test_status_roundtrip(fake_redis: FakeRedis) -> None:
    write_status(fake_redis, {"models": True}, 30)
    assert fake_redis.ttl[READY_KEY] == 30
    assert read_status(fake_redis) == {"models": True}
    fake_redis.data[READY_KEY] = "{не json"
    assert read_status(fake_redis) is None
    fake_redis.data[READY_KEY] = json.dumps([1, 2])
    assert read_status(fake_redis) is None


def test_heartbeat_writes_and_clears(fake_redis: FakeRedis) -> None:
    beats = []

    def payload() -> dict[str, int]:
        beats.append(1)
        return {"n": len(beats)}

    heartbeat = Heartbeat(fake_redis, payload, interval_s=1, ttl_s=3)
    heartbeat.start()
    deadline = time.monotonic() + 5
    while len(beats) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert len(beats) >= 2
    assert read_status(fake_redis)["n"] >= 1
    heartbeat.stop()
    assert READY_KEY not in fake_redis.data


def test_heartbeat_survives_redis_outage(fake_redis: FakeRedis) -> None:
    fake_redis.broken = True
    Heartbeat(fake_redis, lambda: {"models": True}, interval_s=1, ttl_s=3).beat()  # не бросает исключение


def test_payload_without_pipeline_is_not_ready() -> None:
    status = _payload()
    assert status["models"] is False
    assert status["catalog"] is False
    assert status["mode"] == {"extractor": "rules", "reranker": "none", "device": "cpu", "llm_enabled": False}
