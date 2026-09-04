"""Brand-voice and historical-memory retrieval.

The Content Creation Agent pulls tone-of-voice guidance from here before it
writes anything, and the Validation Agent scores drafts against the same
snippets — so both agents agree on what "on brand" means.

Two backends:

* :class:`InMemoryBrandVoiceStore` — lexical scoring, no embeddings, no
  network. The default, and what the test suite uses.
* :class:`QdrantBrandVoiceStore` — production: one collection, one point per
  guideline, filtered by ``user_id``.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import Callable, Iterable, Optional, Protocol, Sequence

from app.core.config import Settings, VectorBackend, get_settings
from app.models.schemas import BrandGuideline, new_id

log = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z0-9']+")
_STOPWORDS = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "has", "have", "if", "in", "into", "is", "it", "its", "of", "on", "or", "our", "that", "the", "their", "this", "to", "was", "we", "were", "will", "with", "you", "your"]
)


def tokenize(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]


class BrandVoiceStore(Protocol):
    async def search(
        self, user_id: str, query: str, *, limit: int = 5
    ) -> list[BrandGuideline]: ...

    async def upsert(self, user_id: str, texts: Sequence[str]) -> list[str]: ...


class InMemoryBrandVoiceStore:
    """Cosine similarity over bag-of-words vectors.

    Not a replacement for embeddings, but it makes retrieval observable in
    tests and keeps the pipeline runnable with no vector DB running.
    """

    def __init__(self) -> None:
        self._docs: dict[str, list[BrandGuideline]] = {}

    async def upsert(self, user_id: str, texts: Sequence[str]) -> list[str]:
        bucket = self._docs.setdefault(user_id, [])
        ids: list[str] = []
        for text in texts:
            text = text.strip()
            if not text:
                continue
            doc = BrandGuideline(doc_id=new_id("bv"), text=text)
            bucket.append(doc)
            ids.append(doc.doc_id)
        return ids

    async def search(
        self, user_id: str, query: str, *, limit: int = 5
    ) -> list[BrandGuideline]:
        docs = self._docs.get(user_id, [])
        if not docs:
            return []
        query_vector = Counter(tokenize(query))
        if not query_vector:
            return docs[:limit]
        scored: list[BrandGuideline] = []
        for doc in docs:
            score = _cosine(query_vector, Counter(tokenize(doc.text)))
            scored.append(doc.model_copy(update={"score": round(score, 6)}))
        scored.sort(key=lambda d: d.score, reverse=True)
        return [doc for doc in scored[:limit] if doc.score > 0] or scored[:limit]


def _cosine(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    shared = set(left) & set(right)
    numerator = sum(left[token] * right[token] for token in shared)
    if not numerator:
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return numerator / (left_norm * right_norm)


class QdrantBrandVoiceStore:
    """Qdrant-backed store, filtered per user.

    ``embed`` turns text into a vector. Supply your own (any embedding
    provider) — the store does not care where the numbers come from.
    """

    def __init__(
        self,
        embed: Callable[[Sequence[str]], "list[list[float]]"],
        settings: Optional[Settings] = None,
    ) -> None:
        from qdrant_client import AsyncQdrantClient  # lazy import

        self.settings = settings or get_settings()
        self.embed = embed
        self._client = AsyncQdrantClient(
            url=self.settings.qdrant_url, api_key=self.settings.qdrant_api_key
        )
        self._collection = self.settings.qdrant_collection

    async def ensure_collection(self) -> None:
        from qdrant_client.models import Distance, VectorParams

        existing = await self._client.collection_exists(self._collection)
        if not existing:
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(
                    size=self.settings.embedding_dimensions, distance=Distance.COSINE
                ),
            )

    async def upsert(self, user_id: str, texts: Sequence[str]) -> list[str]:
        from qdrant_client.models import PointStruct

        await self.ensure_collection()
        vectors = self.embed(list(texts))
        ids = [new_id("bv") for _ in texts]
        await self._client.upsert(
            collection_name=self._collection,
            points=[
                PointStruct(
                    id=doc_id,
                    vector=vector,
                    payload={"user_id": user_id, "text": text},
                )
                for doc_id, vector, text in zip(ids, vectors, texts, strict=True)
            ],
        )
        return ids

    async def search(
        self, user_id: str, query: str, *, limit: int = 5
    ) -> list[BrandGuideline]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        vector = self.embed([query])[0]
        hits = await self._client.search(
            collection_name=self._collection,
            query_vector=vector,
            limit=limit,
            query_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            ),
        )
        return [
            BrandGuideline(
                doc_id=str(hit.id),
                text=str((hit.payload or {}).get("text", "")),
                score=float(hit.score or 0.0),
                metadata={k: v for k, v in (hit.payload or {}).items() if k != "text"},
            )
            for hit in hits
        ]


_store: Optional[BrandVoiceStore] = None

DEFAULT_BRAND_VOICE: tuple[str, ...] = (
    (
        "Speak like a knowledgeable peer: direct, specific, never hypey. "
        "Short sentences. No exclamation marks stacked together."
    ),
    "Always lead with the concrete outcome for the viewer in the first line.",
    "Avoid superlatives we cannot prove, and never promise guaranteed results.",
    "Use plain language over jargon; explain a term the first time it appears.",
    "Close with one clear next step, phrased as an invitation rather than a command.",
)


def get_brand_voice_store() -> BrandVoiceStore:
    """The configured store; falls back to in-memory when Qdrant is not set up."""
    global _store
    if _store is None:
        settings = get_settings()
        if settings.vector_backend is VectorBackend.QDRANT:
            log.info("brand voice store: qdrant at %s", settings.qdrant_url)
            raise RuntimeError(
                "QdrantBrandVoiceStore needs an embedding function; construct it "
                "explicitly and register it with set_brand_voice_store()"
            )
        _store = InMemoryBrandVoiceStore()
    return _store


def set_brand_voice_store(store: Optional[BrandVoiceStore]) -> None:
    global _store
    _store = store


async def seed_default_brand_voice(user_id: str, extra: Iterable[str] = ()) -> list[str]:
    """Give a new account a usable baseline voice so retrieval is never empty."""
    store = get_brand_voice_store()
    return await store.upsert(user_id, [*DEFAULT_BRAND_VOICE, *extra])
