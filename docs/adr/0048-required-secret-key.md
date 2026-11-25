# 0042. An operator can require a secret key of their own

- Status: Accepted
- Date: 2026-11-25
- Origin: hardening for instances that keep their key out of the database

## Context

ADR 0020 generates a key and keeps it in the database when `SECRET_KEY` is missing or a public default, so that no install has to do anything. An operator who keeps the key elsewhere on purpose loses something with that: when the variable goes missing (a renamed secret, a typo in a deploy), the instance starts anyway with another key. Everyone is signed out, new credentials are encrypted under a key that sits next to the data, and nothing says so beyond a log line.

## Decision

- `REQUIRE_SECRET_KEY` (off by default, ADR 0004) makes `resolve_secret_key` in `backend/app/core/config.py` raise when `SECRET_KEY` is missing, blank or a public default. It raises before the database is opened, so nothing is generated or stored.
- A key already stored does not satisfy it. The stored key still opens what it encrypted once the operator sets one of their own (ADR 0020).
- Off, ADR 0020 applies unchanged.

## Consequences

- The API, the worker, beat, the migrate job and the MCP server all read settings the same way, so each needs both variables.
- It amends ADR 0020 only for operators who turn it on.

## Enforced by

`backend/tests/test_instance_secrets.py`: every missing, blank and public value refused with nothing stored, a stored key not accepted, and an own key accepted after it.
