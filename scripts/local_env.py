#!/usr/bin/env python3
"""Discover host ports for the dockerized local development services.

Standalone script — no Django required. Docker Compose maps the db and app
services to host port ranges, so this is the single source of truth for
building local connection URLs. Each subcommand prints exactly one URL on
stdout; progress and errors go to stderr.

Usage:
    local_env.py database-url   Print DATABASE_URL for the running db service
    local_env.py app-url        Print the base URL for the running app service
    local_env.py ensure-db      Start the db service if needed, then print DATABASE_URL
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_URL_TEMPLATE = "postgis://postgres:postgres@localhost:{port}/book_corners"
APP_URL_TEMPLATE = "http://localhost:{port}"


def _compose(*args: str, capture: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a docker compose command anchored at the repository root.
    Anchoring keeps compose project resolution independent of the caller's cwd."""
    try:
        return subprocess.run(
            ["docker", "compose", *args],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        sys.exit("docker not found on PATH; install Docker to use local port discovery")


def _host_port(service: str, container_port: int) -> int:
    """Return the host port currently mapped to a service's container port.
    Exits with guidance on stderr when the service is not running."""
    result = _compose("port", service, str(container_port))
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if result.returncode != 0 or not lines:
        sys.exit(
            f'service "{service}" is not running '
            f"(start it with: docker compose up -d {service})"
        )
    port_text = lines[0].rsplit(":", 1)[-1]
    if not port_text.isdigit() or int(port_text) == 0:
        sys.exit(f"could not parse a host port from {lines[0]!r}")
    return int(port_text)


def _ensure_db() -> int:
    """Start the db service if needed and wait for its healthcheck.
    Idempotent: compose only recreates the container when its config changed."""
    result = _compose("up", "-d", "--wait", "db", capture=False)
    if result.returncode != 0:
        sys.exit("failed to start the db service; check `docker compose logs db`")
    return _host_port(service="db", container_port=5432)


def main() -> None:
    """Parse the subcommand and print the discovered URL on stdout.
    Only the URL is printed to stdout so callers can capture it directly."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["database-url", "app-url", "ensure-db"])
    args = parser.parse_args()
    if args.command == "database-url":
        print(DB_URL_TEMPLATE.format(port=_host_port(service="db", container_port=5432)))
    elif args.command == "ensure-db":
        print(DB_URL_TEMPLATE.format(port=_ensure_db()))
    else:
        print(APP_URL_TEMPLATE.format(port=_host_port(service="app", container_port=8000)))


if __name__ == "__main__":
    main()

