# Образы бэкенда. Сборка из корня репозитория:
#   docker build -f backend/Dockerfile .              → ktru-backend: api, worker-io, beat, migrate
#   docker build -f backend/Dockerfile --target ml .  → ktru-backend-ml: worker-ml (+ torch CPU и модель эмбеддингов)
# Команду сервиса задаёт docker-compose.yml. В образ попадают только backend/, contracts/ и ml/ (см. .dockerignore).

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

# --- Зависимости worker-ml: то же плюс torch (CPU-сборка) и sentence-transformers, модель эмбеддингов -------
FROM ${PYTHON_IMAGE} AS deps-ml
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY backend/requirements-ml.txt /tmp/requirements.txt
# torch==…+cpu есть только в индексе PyTorch; хеши из lock-файла проверяются и для него.
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --require-hashes --only-binary=:all: --no-deps \
      --extra-index-url https://download.pytorch.org/whl/cpu -r /tmp/requirements.txt \
 && /opt/venv/bin/pip uninstall --yes pip
# Модель скачивается при сборке, а не при старте: сеть нужна там же, где и для pip.
COPY backend/fetch_embedder.py /tmp/fetch_embedder.py
RUN python /tmp/fetch_embedder.py /opt/models/multilingual-e5-small

# --- Общая часть итоговых образов -----------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS base
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/srv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp

# Пользователь без прав и без оболочки входа; каталоги томов принадлежат ему (тома наследуют владельца).
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin app \
 && install -d -o app -g app -m 0750 /data/uploads /data/models /data/catalog \
 && python -m pip uninstall --yes pip
WORKDIR /srv
EXPOSE 8000
CMD ["uvicorn", "--factory", "backend.app.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--no-access-log"]

# --- worker-ml ------------------------------------------------------------------------------------------------
FROM base AS ml
LABEL org.opencontainers.image.title="ktru-backend-ml" \
      org.opencontainers.image.description="AI-классификатор КТРУ: ML-воркер (torch CPU, эмбеддинги e5-small)"
# Модель лежит в образе: Hugging Face при работе не нужен.
ENV EMBEDDER_MODEL=/opt/models/multilingual-e5-small \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1
COPY --from=deps-ml /opt/venv /opt/venv
COPY --from=deps-ml /opt/models /opt/models
# Код принадлежит root и только читается: процесс приложения не может его изменить.
COPY contracts/ contracts/
COPY ml/ ml/
COPY backend/ backend/
RUN python -m compileall -q contracts ml backend
USER app:app

# --- api, worker-io, beat, migrate (последняя стадия — образ по умолчанию) ------------------------------------
FROM base AS runtime
LABEL org.opencontainers.image.title="ktru-backend" \
      org.opencontainers.image.description="AI-классификатор КТРУ: API, воркеры Celery, миграции"
COPY --from=deps /opt/venv /opt/venv
# Код принадлежит root и только читается: процесс приложения не может его изменить.
COPY contracts/ contracts/
COPY ml/ ml/
COPY backend/ backend/
RUN python -m compileall -q contracts ml backend
USER app:app
