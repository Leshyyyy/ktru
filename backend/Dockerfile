# Образ бэкенда: api, worker-io, worker-ml, beat и migrate (команду задаёт docker-compose.yml).
# Сборка из корня репозитория: docker build -f backend/Dockerfile .
# В образ попадают только backend/, contracts/ и ml/ (см. .dockerignore).

ARG PYTHON_IMAGE=python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

# --- Зависимости: отдельный слой, пересобирается только при смене lock-файла ---------------------------------
FROM ${PYTHON_IMAGE} AS deps
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY backend/requirements.txt /tmp/requirements.txt
# Только готовые колёса с проверкой хешей из lock-файла: при сборке не выполняется чужой setup.py,
# а подменённый пакет не установится. pip из итогового окружения удаляем — в работе он не нужен.
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --require-hashes --only-binary=:all: --no-deps -r /tmp/requirements.txt \
 && /opt/venv/bin/pip uninstall --yes pip

# --- Итоговый образ -----------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="ktru-backend" \
      org.opencontainers.image.description="AI-классификатор КТРУ: API, воркеры Celery, миграции"

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/srv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp

# Пользователь без прав и без оболочки входа; каталоги томов принадлежат ему (тома наследуют владельца).
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin app \
 && install -d -o app -g app -m 0750 /data/uploads /data/models \
 && python -m pip uninstall --yes pip

COPY --from=deps /opt/venv /opt/venv
WORKDIR /srv
# Код принадлежит root и только читается: процесс приложения не может его изменить.
COPY contracts/ contracts/
COPY ml/ ml/
COPY backend/ backend/
RUN python -m compileall -q contracts ml backend

USER app:app
EXPOSE 8000
CMD ["uvicorn", "--factory", "backend.app.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--no-access-log"]
