"""Эмбеддеры. По умолчанию поиск работает на TF-IDF (retrieve.py) и моделей не требует;
этот модуль нужен, когда задан EMBEDDER_MODEL, и для эмбеддингов каталога в Postgres (vector(1024))."""

from __future__ import annotations


class SentenceTransformerEmbedder:
    """Embedder из ml_contracts поверх sentence-transformers (e5, bge-m3). Импорт ленивый: torch нужен только здесь."""

    def __init__(self, model_name: str, device: str = "cpu", batch_size: int = 64):
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self.model_name = model_name
        self.model = SentenceTransformer(model_name, device=device)
        self.dim = int(self.model.get_sentence_embedding_dimension())
        self.batch_size = batch_size
        # Модели семейства e5 обучены с префиксами «query:» / «passage:».
        self._e5 = "e5" in model_name.lower()

    def embed(self, texts: list[str], kind: str = "query") -> list[list[float]]:
        if self._e5:
            texts = [f"{kind}: {t}" for t in texts]
        vecs = self.model.encode(texts, batch_size=self.batch_size, normalize_embeddings=True, show_progress_bar=False)
        return vecs.tolist()
