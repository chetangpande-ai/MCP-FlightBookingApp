"""MCP server for searching demo company travel policies."""

from functools import lru_cache
from math import sqrt
import re
from collections import Counter

from mcp.server import MCPServer


server = MCPServer("Business Trip Policy MCP Server")

POLICY_DOCUMENTS = (
    {
        "title": "Flight class",
        "text": (
            "Employees should book economy class for business flights. Business class "
            "requires manager approval for flights longer than six hours."
        ),
    },
    {
        "title": "Hotel budget",
        "text": (
            "Hotel costs should not exceed INR 12,000 per night, including taxes. "
            "A higher rate requires manager approval before booking."
        ),
    },
    {
        "title": "Expense reimbursement",
        "text": (
            "Submit travel expense claims with itemized receipts within 10 business days "
            "after the trip. Claims without receipts may be rejected."
        ),
    },
    {
        "title": "Leave",
        "text": (
            "Employees must record personal leave that is attached to a business trip "
            "in the company leave system and receive manager approval."
        ),
    },
)


@lru_cache(maxsize=1)
def get_document_vectors() -> tuple[tuple[float, ...], ...]:
    """Build local TF-IDF vectors once and reuse them in this process."""
    tokenized_documents = [
        re.findall(r"[a-z0-9]+", document["text"].lower())
        for document in POLICY_DOCUMENTS
    ]
    document_frequency = Counter(
        token for tokens in tokenized_documents for token in set(tokens)
    )
    vocabulary = sorted(document_frequency)
    document_count = len(tokenized_documents)

    vectors = []
    for tokens in tokenized_documents:
        term_counts = Counter(tokens)
        vector = tuple(
            (1 + sqrt(term_counts[token]))
            * (1 + document_count / document_frequency[token])
            for token in vocabulary
        )
        vectors.append(vector)
    return tuple(vectors)


def text_vector(text: str) -> tuple[float, ...]:
    """Vectorize query text with the same vocabulary as the policy documents."""
    token_counts = Counter(re.findall(r"[a-z0-9]+", text.lower()))
    document_frequency = Counter(
        token
        for document in POLICY_DOCUMENTS
        for token in set(re.findall(r"[a-z0-9]+", document["text"].lower()))
    )
    vocabulary = sorted(document_frequency)
    document_count = len(POLICY_DOCUMENTS)
    return tuple(
        (1 + sqrt(token_counts[token]))
        * (1 + document_count / document_frequency[token])
        if token_counts[token]
        else 0.0
        for token in vocabulary
    )


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """Return cosine similarity for two local TF-IDF vectors."""
    dot_product = sum(left_value * right_value for left_value, right_value in zip(left, right))
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot_product / (left_norm * right_norm)


# TOOLS = DO
@server.tool()
def search_company_policy(query: str, top_k: int = 2) -> list[dict[str, str | float]]:
    """Return the most relevant demo policy documents and cosine scores."""
    if not query.strip() or top_k <= 0:
        return []

    query_vector = text_vector(query)
    document_vectors = get_document_vectors()

    scored_documents = []
    for document, vector in zip(POLICY_DOCUMENTS, document_vectors):
        scored_documents.append(
            {
                "title": document["title"],
                "text": document["text"],
                "score": cosine_similarity(vector, query_vector),
            }
        )

    return sorted(scored_documents, key=lambda item: item["score"], reverse=True)[:top_k]


# RESOURCES = KNOW
@server.resource("policy://travel-summary")
def travel_policy_summary() -> str:
    """Return a short fixed summary of the demo travel policies."""
    return (
        "Book economy flights by default. Keep hotels within INR 12,000 per night. "
        "Submit itemized expense receipts within 10 business days. Record and obtain "
        "approval for personal leave attached to a business trip."
    )


if __name__ == "__main__":
    server.run()
