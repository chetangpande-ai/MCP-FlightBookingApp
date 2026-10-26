"""Input validation, idempotency, and weather failure tests."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from typing import Any, Generator

import pytest

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from servers import idempotency, policy_server, travel_server, work_server


@pytest.fixture
def idempotency_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    database_path = tmp_path / "idempotency.sqlite3"
    monkeypatch.setattr(idempotency, "DEFAULT_DATABASE_PATH", database_path)
    return database_path


@pytest.fixture
def clean_trip_tasks() -> Iterator[None]:
    existing_tasks = work_server.trip_tasks.copy()
    work_server.trip_tasks.clear()
    try:
        yield
    finally:
        work_server.trip_tasks[:] = existing_tasks


@contextmanager
def _approved_booking_context() -> Generator[None, None, None]:
    access_token = AccessToken(
        token="verified-test-approval",
        client_id="test-approver",
        scopes=["travel:read", "travel:book"],
        expires_at=None,
        resource="http://127.0.0.1:8001/mcp",
        subject="test-approver",
        claims={},
    )
    context_token = auth_context_var.set(AuthenticatedUser(access_token))
    try:
        yield
    finally:
        auth_context_var.reset(context_token)


def _approved_hotel(*args: Any, **kwargs: Any) -> dict[str, str | int]:
    with _approved_booking_context():
        return travel_server.book_hotel(*args, **kwargs)


def test_duplicate_hotel_booking_returns_original_result(
    idempotency_database: Path,
) -> None:
    first = _approved_hotel(
        "Dubai", 4, "hotel-booking-123", hotel_class="4-star"
    )
    retry = _approved_hotel(
        "Dubai", 4, "hotel-booking-123", hotel_class="4-star"
    )

    assert retry == first
    assert idempotency_database.exists()


@pytest.mark.usefixtures("idempotency_database")
def test_hotel_idempotency_key_cannot_be_reused_for_different_booking() -> None:
    _approved_hotel("Dubai", 4, "hotel-booking-456")

    with pytest.raises(ValueError, match="different parameters"):
        _approved_hotel("Paris", 4, "hotel-booking-456")


@pytest.mark.parametrize("nights", [0, 31, True, 2.5, "2"])
def test_hotel_rejects_invalid_night_counts(
    idempotency_database: Path,
    nights: int | bool | float | str,
) -> None:
    with pytest.raises(ValueError, match="between 1 and 30"):
        _approved_hotel("Dubai", nights, "invalid-nights-key")
    assert not idempotency_database.exists()


@pytest.mark.parametrize("hotel_class", ["economy", "6-star", "", "4-Star"])
def test_hotel_rejects_unknown_hotel_class(
    idempotency_database: Path,
    hotel_class: str,
) -> None:
    with pytest.raises(ValueError, match="hotel_class"):
        _approved_hotel(
            "Dubai", 3, "invalid-class-key", hotel_class=hotel_class
        )
    assert not idempotency_database.exists()


def test_hotel_rejects_empty_or_oversized_city(
    idempotency_database: Path,
) -> None:
    with pytest.raises(ValueError, match="city must not be empty"):
        _approved_hotel("  ", 3, "empty-city-key")

    with pytest.raises(ValueError, match="city must be at most"):
        _approved_hotel("D" * 101, 3, "long-city-key")
    assert not idempotency_database.exists()


def test_tool_rejects_invalid_idempotency_key(
    idempotency_database: Path,
) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        _approved_hotel("Dubai", 2, "  ")

    with pytest.raises(ValueError, match="at most 128"):
        _approved_hotel("Dubai", 2, "k" * 129)
    assert not idempotency_database.exists()


def test_hotel_booking_without_verified_approval_is_rejected() -> None:
    with pytest.raises(PermissionError, match="travel:book"):
        travel_server.book_hotel("Dubai", 2, "unapproved-booking-key")


@pytest.mark.usefixtures("idempotency_database", "clean_trip_tasks")
def test_duplicate_trip_task_is_not_appended_twice() -> None:
    assert work_server.trip_tasks == []
    first = work_server.create_trip_task(
        "Confirm meeting time", "meeting-task-1", owner="Alex"
    )
    retry = work_server.create_trip_task(
        "Confirm meeting time", "meeting-task-1", owner="Alex"
    )

    assert retry == first
    assert work_server.list_trip_tasks() == [first]


@pytest.mark.usefixtures("idempotency_database", "clean_trip_tasks")
def test_trip_task_key_cannot_be_reused_for_different_parameters() -> None:
    assert work_server.trip_tasks == []
    work_server.create_trip_task("Prepare slides", "meeting-task-2")

    with pytest.raises(ValueError, match="different parameters"):
        work_server.create_trip_task("Book room", "meeting-task-2")


@pytest.mark.usefixtures("idempotency_database", "clean_trip_tasks")
def test_trip_task_inputs_and_result_count_are_bounded() -> None:
    assert work_server.trip_tasks == []
    with pytest.raises(ValueError, match="title must not be empty"):
        work_server.create_trip_task(" ", "empty-task-title")

    for index in range(work_server.MAX_TASKS_RETURNED + 3):
        work_server.create_trip_task(f"Task {index}", f"task-key-{index}")

    assert len(work_server.list_trip_tasks()) == work_server.MAX_TASKS_RETURNED


def test_weather_retries_transient_failure_and_returns_structured_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def failing_urlopen(url: str, timeout: float):
        nonlocal attempts
        attempts += 1
        raise URLError(f"simulated offline network for {url} (timeout {timeout})")

    monkeypatch.setattr(travel_server, "urlopen", failing_urlopen)
    monkeypatch.setattr(travel_server, "sleep", lambda _: None)

    result = travel_server.get_live_weather("Dubai")

    assert attempts == travel_server.WEATHER_MAX_ATTEMPTS
    assert result == {
        "ok": False,
        "error": "Weather service request failed after retries.",
        "retryable": True,
    }


def test_weather_reports_unknown_city_as_non_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(travel_server, "_get_weather_json", lambda _: {"results": []})

    result = travel_server.get_live_weather("Nowhere")

    assert result == {
        "ok": False,
        "error": "No location found for Nowhere.",
        "retryable": False,
    }


def test_weather_rejects_invalid_city_without_network_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_request(url: str, timeout: float):
        raise AssertionError(
            f"weather API should not be called for {url} (timeout {timeout})"
        )

    monkeypatch.setattr(travel_server, "urlopen", unexpected_request)

    result = travel_server.get_live_weather(" ")

    assert result["ok"] is False
    assert result["retryable"] is False


def test_flight_input_is_validated() -> None:
    with pytest.raises(ValueError, match="origin must not be empty"):
        travel_server.search_flights(" ", "Dubai")

    with pytest.raises(ValueError, match="destination must be at most"):
        travel_server.search_flights("Bangalore", "D" * 101)


def test_policy_input_and_output_are_bounded() -> None:
    with pytest.raises(ValueError, match="query must be non-empty"):
        policy_server.search_company_policy(" ")

    with pytest.raises(ValueError, match="top_k must be a positive integer"):
        policy_server.search_company_policy("hotel", top_k=True)

    assert len(policy_server.search_company_policy("hotel", top_k=1000)) <= (
        policy_server.MAX_POLICY_RESULTS
    )

    with pytest.raises(ValueError, match="query must be at most"):
        policy_server.search_company_policy("policy " * 100)


def test_policy_search_returns_cited_short_chunks(tmp_path: Path) -> None:
    results = policy_server.search_policy_documents(
        "hotel nightly budget",
        top_k=1,
        mode="tfidf",
        cache_path=tmp_path / "index.json",
    )

    assert len(results) == 1
    assert results[0]["document_id"] == "hotel-budget"
    assert results[0]["title"] == "Hotel budget"
    assert len(str(results[0]["text"])) <= policy_server.MAX_POLICY_CHUNK_LENGTH
    assert isinstance(results[0]["score"], float)
    assert results[0]["score"] > 0


def test_policy_index_is_reused_and_invalidated_by_content_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_path = tmp_path / "index.json"
    build_index = policy_server._build_index
    build_count = 0

    def counted_build(documents):
        nonlocal build_count
        build_count += 1
        return build_index(documents)

    monkeypatch.setattr(policy_server, "_build_index", counted_build)
    first = policy_server.load_or_build_index(cache_path=cache_path)
    second = policy_server.load_or_build_index(cache_path=cache_path)

    changed_documents = (
        {
            **policy_server.POLICY_DOCUMENTS[0],
            "text": "Economy flights require manager approval.",
        },
        *policy_server.POLICY_DOCUMENTS[1:],
    )
    changed = policy_server.load_or_build_index(
        changed_documents,
        cache_path,
    )

    assert build_count == 2
    assert second == first
    assert changed.content_hash != first.content_hash


def test_offline_keyword_search_needs_no_disk_cache(tmp_path: Path) -> None:
    cache_path = tmp_path / "must-not-be-created.json"

    results = policy_server.search_policy_documents(
        "hotel budget",
        mode="offline",
        cache_path=cache_path,
    )

    assert results[0]["document_id"] == "hotel-budget"
    assert not cache_path.exists()


def test_corrupt_policy_cache_is_rebuilt(tmp_path: Path) -> None:
    cache_path = tmp_path / "index.json"
    cache_path.write_text("{invalid json", encoding="utf-8")

    index = policy_server.load_or_build_index(cache_path=cache_path)

    assert index.content_hash
    assert cache_path.exists()
    assert policy_server.load_or_build_index(cache_path=cache_path) == index


def test_policy_search_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="mode must be"):
        policy_server.search_policy_documents("hotel", mode="online")
