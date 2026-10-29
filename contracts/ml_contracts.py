"""
Контракты между бэкендом и ML-частью.

Бэкенд вызывает ТОЛЬКО Pipeline.classify_batch. Всё остальное (Extractor, Embedder,
Retriever, Reranker, Calibrator) — внутренности ML-команды, но их интерфейсы зафиксированы
здесь, чтобы компоненты можно было разрабатывать и заменять независимо.

Правила:
  * Уверенность (confidence) — всегда float от 0 до 1 и означает оценку вероятности того,
    что именно этот код верный. Поэтому сумма confidence по трём предсказаниям позиции не больше 1.
    Калибратор применяется к признакам каждого кандидата отдельно; если сумма вышла больше 1,
    значения нормируются делением на сумму.
  * confidence не возрастает с ростом rank: у rank 1 она наибольшая. Бэкенд проверяет это при приёме ответа.
  * Все методы пакетные: принимают и возвращают списки одинаковой длины и порядка.
  * Ни один метод не должен падать из-за одной плохой позиции: ошибка по позиции
    возвращается в ClassificationResult.error, остальные позиции пакета обрабатываются.
  * Изменения этого файла — отдельным PR с одобрением бэкенда и ML.
"""

from __future__ import annotations

from typing import Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Типы данных
# ---------------------------------------------------------------------------

ExtractionMethod = Literal["rules", "llm", "hybrid"]
CharStatus = Literal["match", "mismatch", "missing"]
RoutePath = Literal["fast", "rerank"]
Mode = Literal["normal", "degraded"]
ItemFlag = Literal["required_missing", "archived_code", "low_extraction_confidence"]

# Признаки, которые ConfidenceCalibrator принимает и которые сохраняются в predictions.features.
FEATURE_NAMES = (
    "top1_fused",          # объединённая оценка поиска у первого кандидата
    "margin_top1_top2",    # отрыв первого кандидата от второго
    "vector_score",        # косинусное сходство
    "text_score",          # оценка текстового поиска
    "char_match_ratio",    # доля совпавших характеристик КТРУ (0, если характеристик нет)
    "rerank_score",        # оценка реранкера (0, если пути rerank не было)
    "extraction_confidence",
    "vec_text_agreement",  # 1.0, если первые кандидаты векторного и текстового поиска совпали
    "confirmed_vote",      # близость к ранее подтверждённому примеру (0..1)
)


class RawItem(BaseModel):
    """Одна позиция из файла, после разбора и очистки."""

    item_id: str
    text: str = Field(description="Склейка name + description + characteristics.")
    columns: dict[str, str] = Field(
        default_factory=dict,
        description="Исходные значения по ролям колонок: name, description, characteristics, quantity, unit.",
    )


class ExtractedAttributes(BaseModel):
    name: str
    brand: Optional[str] = None
    model: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    characteristics: dict[str, str] = Field(default_factory=dict)
    method: ExtractionMethod = "rules"
    field_confidence: dict[str, float] = Field(
        default_factory=dict, description="Уверенность по полям: name, brand, model."
    )


class SearchQuery(BaseModel):
    text: str
    extracted: ExtractedAttributes
    catalog_version_id: str
    top_k: int = 20


class Candidate(BaseModel):
    ktru_code: str
    name: str
    okpd2_code: str
    vector_score: float = 0.0
    text_score: float = 0.0
    fused_score: float = 0.0
    characteristics: list[dict] = Field(
        default_factory=list, description='[{"name": "...", "values": ["..."], "required": true}]'
    )


class CharacteristicMatch(BaseModel):
    name: str
    expected: Optional[str] = Field(None, description="Что допускает КТРУ.")
    found: Optional[str] = Field(None, description="Что найдено в позиции.")
    status: CharStatus
    required: bool = Field(False, description="Характеристика обязательна по КТРУ.")


class RerankResult(BaseModel):
    ordered_codes: list[str] = Field(description="Коды кандидатов от лучшего к худшему.")
    scores: list[float] = Field(description="Оценка реранкера для каждого кода в том же порядке.")
    rationale: Optional[str] = None


class Prediction(BaseModel):
    rank: int = Field(ge=1, le=3)
    ktru_code: str
    name: str
    okpd2_code: str
    confidence: float = Field(ge=0.0, le=1.0)
    features: dict[str, float] = Field(default_factory=dict)
    explanation: Optional[str] = Field(
        None,
        description="Все названные атрибуты и характеристики должны быть в extracted/characteristic_matches "
        "(проверяет валидатор конвейера); иначе объяснение отбрасывается.",
    )
    why_not_chosen: Optional[str] = Field(None, description="Для rank 2 и 3: почему хуже первого.")
    characteristic_matches: list[CharacteristicMatch] = Field(default_factory=list)


class ClassificationResult(BaseModel):
    item_id: str
    extracted: Optional[ExtractedAttributes] = None
    predictions: list[Prediction] = Field(default_factory=list, description="До 3, от лучшего.")
    path: Optional[RoutePath] = None
    norm_key: Optional[str] = Field(
        None,
        description="lower(name)|lower(brand)|lower(model); None, если нет бренда или модели. Наименование "
        "входит в ключ: иначе принтер и картридж к нему слились бы. Позиции с одинаковым ключом бэкенд "
        "объединяет в группу (group_id). Внутри одного вызова конвейер обрабатывает один раз только позиции "
        "с одинаковым нормализованным текстом целиком.",
    )
    flags: list[ItemFlag] = Field(
        default_factory=list,
        description="required_missing — у rank 1 есть обязательная характеристика со статусом missing.",
    )
    mode: Mode = Field(
        "normal",
        description="degraded — LLM была нужна, но недоступна; бэкенд ставит jobs.effective_mode = degraded.",
    )
    timings_ms: dict[str, float] = Field(
        default_factory=dict, description="Ключи: extract, embed, retrieve, rerank, calibrate, total."
    )
    error: Optional[str] = Field(None, description="Если задано — позиция получит status=failed.")


class PipelineOptions(BaseModel):
    use_llm: bool = True
    top_k: int = 20
    fast_path_min_score: float = 0.75
    fast_path_min_margin: float = 0.15
    use_feedback: bool = Field(
        True, description="Использовать confirmed_examples (кэш точных совпадений). В оценке качества — False."
    )


# ---------------------------------------------------------------------------
# Интерфейсы компонентов (реализует ML-команда)
# ---------------------------------------------------------------------------


@runtime_checkable
class AttributeExtractor(Protocol):
    def extract_batch(self, items: list[RawItem]) -> list[ExtractedAttributes]: ...


@runtime_checkable
class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Нормализованные векторы длины dim."""
        ...


@runtime_checkable
class Retriever(Protocol):
    def search_batch(self, queries: list[SearchQuery]) -> list[list[Candidate]]: ...


@runtime_checkable
class Reranker(Protocol):
    def rerank_batch(
        self, pairs: list[tuple[ExtractedAttributes, list[Candidate]]]
    ) -> list[RerankResult]: ...


@runtime_checkable
class ConfidenceCalibrator(Protocol):
    def predict(self, features: dict[str, float]) -> float:
        """Признаки из FEATURE_NAMES → вероятность 0..1."""
        ...


def normalize_text(text: str) -> str:
    """Единственная функция нормализации: нижний регистр, схлопнутые пробелы, без краевых знаков препинания.

    Используется и при записи в confirmed_examples.norm_text, и при поиске по кэшу точных совпадений.
    """
    return " ".join(text.lower().replace("ё", "е").split()).strip(" .,;:")


class CatalogRow(BaseModel):
    """Строка JSONL для импорта каталога (то же, что схема CatalogRow в openapi.yaml)."""

    ktru_code: str
    okpd2_code: str
    name: str
    description: Optional[str] = None
    unit: Optional[str] = None
    status: Literal["active", "archived"] = "active"
    replaced_by: Optional[str] = None
    characteristics: list[dict] = Field(default_factory=list)


@runtime_checkable
class CatalogIndexer(Protocol):
    def index_version(self, catalog_version_id: str) -> int:
        """Строит search_text и эмбеддинги для строк версии, у которых embedding IS NULL. Возвращает число обработанных."""
        ...

    def index_examples(self) -> int:
        """Дозаполняет embedding у confirmed_examples. Возвращает число обработанных."""
        ...


# ---------------------------------------------------------------------------
# Единственная точка входа для бэкенда
# ---------------------------------------------------------------------------


@runtime_checkable
class Pipeline(Protocol):
    def classify_batch(
        self,
        items: list[RawItem],
        catalog_version_id: str,
        options: PipelineOptions | None = None,
    ) -> list[ClassificationResult]:
        """Возвращает по одному результату на позицию, в том же порядке."""
        ...

    def is_ready(self) -> dict[str, bool]:
        """Для GET /health: {'models': bool, 'catalog': bool}."""
        ...
