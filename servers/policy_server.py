"""MCP server for searching demo company travel policies."""

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import logging
from math import isfinite, sqrt
from pathlib import Path
import re
import tempfile
from typing import Any

from mcp.server import MCPServer

from observability import observe_tool
from settings import settings


MAX_POLICY_QUERY_LENGTH = 500
MAX_POLICY_RESULTS = 10
MAX_POLICY_CHUNK_LENGTH = 500
POLICY_INDEX_SCHEMA_VERSION = 1
HOTEL_BUDGET_INR = 12_000
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
logger = logging.getLogger(__name__)
server = MCPServer("Business Trip Policy MCP Server")

POLICY_DOCUMENTS = (
    {
        "id": "flight-class",
        "title": "Flight class",
        "text": (
            "Employees should book economy class for business flights. Business class "
            "requires manager approval for flights longer than six hours."
        ),
    },
    {
        "id": "hotel-budget",
        "title": "Hotel budget",
        "text": (
            f"Hotel costs should not exceed INR {HOTEL_BUDGET_INR:,} per night, "
            "including taxes. A higher rate requires manager approval before booking."
        ),
    },
    {
        "id": "expense-reimbursement",
        "title": "Expense reimbursement",
        "text": (
            "Submit travel expense claims with itemized receipts within 10 business days "
            "after the trip. Claims without receipts may be rejected."
        ),
    },
    {
        "id": "leave",
        "title": "Leave",
        "text": (
            "Employees must record personal leave that is attached to a business trip "
            "in the company leave system and receive manager approval."
        ),
    },
)


@dataclass(frozen=True)
class PolicyIndex:
    """Token vocabulary and document vectors for the current policy corpus."""

    content_hash: str
    vocabulary: tuple[str, ...]
    document_frequency: tuple[int, ...]
    document_vectors: tuple[tuple[float, ...], ...]


def get_hotel_nightly_limit_inr() -> int:
    """Return the nightly hotel limit used by both policy text and approval checks."""
    return HOTEL_BUDGET_INR


def _tokenize(text: str) -> list[str]:
    return TOKEN_PATTERN.findall(text.lower())


def _corpus_hash(
    documents: tuple[dict[str, str], ...],
) -> str:
    canonical = json.dumps(
        {
            "schema_version": POLICY_INDEX_SCHEMA_VERSION,
            "documents": documents,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_index(documents: tuple[dict[str, str], ...]) -> PolicyIndex:
    tokenized_documents = [_tokenize(document["text"]) for document in documents]
    document_frequency_by_token = Counter(
        token
        for tokens in tokenized_documents
        for token in set(tokens)
    )
    vocabulary = tuple(sorted(document_frequency_by_token))
    document_frequency = tuple(
        document_frequency_by_token[token] for token in vocabulary
    )
    total_documents = len(tokenized_documents)
    document_vectors: list[tuple[float, ...]] = []

    for tokens in tokenized_documents:
        term_counts = Counter(tokens)
        vector = tuple(
            (1 + sqrt(term_counts[token]))
            * (1 + total_documents / document_frequency_by_token[token])
            for token in vocabulary
        )
        document_vectors.append(vector)

    return PolicyIndex(
        content_hash=_corpus_hash(documents),
        vocabulary=vocabulary,
        document_frequency=document_frequency,
        document_vectors=tuple(document_vectors),
    )


def _index_from_cache(
    cache_data: Any,
    *,
    expected_hash: str,
    documents: tuple[dict[str, str], ...],
) -> PolicyIndex | None:
    if not isinstance(cache_data, dict):
        return None
    if (
        cache_data.get("schema_version") != POLICY_INDEX_SCHEMA_VERSION
        or cache_data.get("content_hash") != expected_hash
    ):
        return None

    vocabulary = cache_data.get("vocabulary")
    document_frequency = cache_data.get("document_frequency")
    document_vectors = cache_data.get("document_vectors")
    if not (
        isinstance(vocabulary, list)
        and all(isinstance(token, str) for token in vocabulary)
        and isinstance(document_frequency, list)
        and all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in document_frequency
        )
        and isinstance(document_vectors, list)
        and len(document_vectors) == len(documents)
    ):
        return None
    if len(vocabulary) != len(document_frequency):
        return None
    if any(not isinstance(vector, list) for vector in document_vectors):
        return None
    if any(len(vector) != len(vocabulary) for vector in document_vectors):
        return None
    if any(value <= 0 or value > len(documents) for value in document_frequency):
        return None
    if vocabulary != sorted(set(vocabulary)):
        return None
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(value)
        for vector in document_vectors
        for value in vector
    ):
        return None

    return PolicyIndex(
        content_hash=expected_hash,
        vocabulary=tuple(vocabulary),
        document_frequency=tuple(document_frequency),
        document_vectors=tuple(
            tuple(float(value) for value in vector) for vector in document_vectors
        ),
    )


def _write_index_cache(index: PolicyIndex, cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_data = {
        "schema_version": POLICY_INDEX_SCHEMA_VERSION,
        "content_hash": index.content_hash,
        "vocabulary": index.vocabulary,
        "document_frequency": index.document_frequency,
        "document_vectors": index.document_vectors,
    }
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=cache_path.parent,
            prefix=f".{cache_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as cache_file:
            temporary_path = Path(cache_file.name)
            json.dump(
                cache_data,
                cache_file,
                sort_keys=True,
                separators=(",", ":"),
            )
        temporary_path.replace(cache_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def load_or_build_index(
    documents: tuple[dict[str, str], ...] = POLICY_DOCUMENTS,
    cache_path: Path | None = None,
) -> PolicyIndex:
    """Load a content-matched local index or build and persist a fresh one."""
    path = cache_path or settings.policy_cache_path
    expected_hash = _corpus_hash(documents)
    if path.exists():
        try:
            cached_data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Policy vector cache is invalid; rebuilding it.")
        else:
            index = _index_from_cache(
                cached_data,
                expected_hash=expected_hash,
                documents=documents,
            )
            if index is not None:
                return index

    index = _build_index(documents)
    _write_index_cache(index, path)
    return index


def text_vector(
    text: str,
    index: PolicyIndex,
    total_documents: int,
) -> tuple[float, ...]:
    """Vectorize query text using the current cached vocabulary."""
    token_counts = Counter(_tokenize(text))
    return tuple(
        (1 + sqrt(token_counts[token]))
        * (1 + total_documents / document_frequency)
        if token_counts[token]
        else 0.0
        for token, document_frequency in zip(
            index.vocabulary,
            index.document_frequency,
        )
    )


def cosine_similarity(
    left: tuple[float, ...],
    right: tuple[float, ...],
) -> float:
    """Return cosine similarity for two local TF-IDF vectors."""
    dot_product = sum(
        left_value * right_value
        for left_value, right_value in zip(left, right)
    )
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot_product / (left_norm * right_norm)


def _keyword_score(query_tokens: set[str], document_text: str) -> float:
    document_tokens = set(_tokenize(document_text))
    if not query_tokens:
        return 0.0
    return len(query_tokens & document_tokens) / len(query_tokens)


def search_policy_documents(
    query: str,
    top_k: int = 2,
    *,
    mode: str | None = None,
    cache_path: Path | None = None,
    documents: tuple[dict[str, str], ...] = POLICY_DOCUMENTS,
) -> list[dict[str, str | float]]:
    """Search the local corpus in TF-IDF mode or the no-cache offline keyword mode."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be non-empty text.")
    if len(query) > MAX_POLICY_QUERY_LENGTH:
        raise ValueError(
            f"query must be at most {MAX_POLICY_QUERY_LENGTH} characters."
        )
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("top_k must be a positive integer.")

    search_mode = settings.policy_search_mode if mode is None else mode
    if search_mode not in {"tfidf", "offline"}:
        raise ValueError("mode must be 'tfidf' or 'offline'.")
    top_k = min(top_k, MAX_POLICY_RESULTS)
    query_tokens = set(_tokenize(query))

    if search_mode == "offline":
        scored_documents = [
            (
                document,
                _keyword_score(query_tokens, document["text"]),
            )
            for document in documents
        ]
    else:
        index = load_or_build_index(documents, cache_path)
        query_vector = text_vector(query, index, len(documents))
        scored_documents = [
            (
                document,
                cosine_similarity(vector, query_vector),
            )
            for document, vector in zip(documents, index.document_vectors)
        ]

    return [
        {
            "document_id": document["id"],
            "title": document["title"],
            "text": document["text"][:MAX_POLICY_CHUNK_LENGTH],
            "score": round(score, 6),
        }
        for document, score in sorted(
            scored_documents,
            key=lambda item: item[1],
            reverse=True,
        )[:top_k]
    ]


# TOOLS = DO
@server.tool()
@observe_tool
def search_company_policy(query: str, top_k: int = 2) -> list[dict[str, str | float]]:
    """Return short cited policy chunks ranked by local relevance."""
    return search_policy_documents(query, top_k)


# RESOURCES = KNOW
@server.resource("policy://travel-summary")
def travel_policy_summary() -> str:
    """Return a short fixed summary of the demo travel policies."""
    return (
        f"Book economy flights by default. Keep hotels within INR {HOTEL_BUDGET_INR:,} "
        "per night. Submit itemized expense receipts within 10 business days. "
        "Record and obtain approval for personal leave attached to a business trip."
    )


if __name__ == "__main__":
    server.run()
