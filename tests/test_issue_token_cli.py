"""Tests for the local token issuer's safe defaults and scope restrictions."""

import pytest

from scripts.issue_token import build_parser


def test_default_token_scopes_are_read_only() -> None:
    args = build_parser().parse_args(
        ["--server-url", "http://127.0.0.1:8001/mcp"]
    )

    assert args.scope == ["travel:read"]


def test_cli_cannot_issue_booking_scope() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "--server-url",
                "http://127.0.0.1:8001/mcp",
                "--scope",
                "travel:book",
            ]
        )
