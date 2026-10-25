"""Issue a signed JWT for local MCP authorization tests and demos."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auth.tokens import issue_token
from settings import settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Issue a local JWT for an MCP server.")
    parser.add_argument("--server-url", required=True, help="Server resource URL to mint the token for.")
    parser.add_argument("--subject", default="local-agent", help="JWT subject claim.")
    parser.add_argument(
        "--scope",
        nargs="+",
        default=["travel:read"],
        choices=["travel:read", "policy:read", "work:write"],
        help="Scopes to include in the token; use space-separated values.",
    )
    parser.add_argument(
        "--expires-minutes",
        type=int,
        default=30,
        help="How long until the token expires, in minutes.",
    )
    parser.add_argument(
        "--secret",
        default=settings.token_secret,
        help="JWT signing secret. Defaults to the project TOKEN_SECRET setting.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    secret = args.secret or os.getenv("TOKEN_SECRET") or settings.token_secret
    if not secret:
        raise SystemExit("Missing TOKEN_SECRET in .env or environment before issuing a token.")

    token = issue_token(
        args.server_url,
        args.subject,
        args.scope,
        secret=secret,
        expires_minutes=args.expires_minutes,
    )
    print(token)


if __name__ == "__main__":
    main()
