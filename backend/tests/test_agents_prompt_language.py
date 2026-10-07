"""The text the model reads must be language-neutral.

Securo ships in 15 UI languages, and the runtime guardrail tells the model
to answer in the user's preferred language. Examples written in one
language (or one currency) inside the system prompt and tool descriptions
pull the model toward that language — a user who writes in English can get
a reply that drifts into Portuguese phrasing or formats money as R$. These
tests pin the prompt sources to plain English so every locale starts from
the same neutral baseline.
"""
from __future__ import annotations

import re
import uuid

import pytest

import mcp_server.tools  # noqa: F401  (registers the tools)
from app.agents.runtime.executor import _RUNTIME_GUARDRAIL, _build_agent_identity_primer, _format_page_context
from app.agents.services.context_service import _language_label
from mcp_server.registry import REGISTRY

# Typographic punctuation is fine; letters outside ASCII are not.
_ALLOWED_NON_ASCII = set("—–…→←'‘’“”•·×")
# Words that only appear when an example was written in Portuguese.
_FOREIGN_TOKENS = re.compile(
    r"\b(não|você|aqui|proposta|prévia|preparei|segundo|grupo|quem|saldo|quites|crie|divida)\b|R\$",
    re.IGNORECASE,
)


def _assert_neutral(text: str, where: str) -> None:
    foreign = [c for c in text if ord(c) > 127 and c not in _ALLOWED_NON_ASCII]
    assert not foreign, f"{where}: non-ASCII letters {sorted(set(foreign))!r}"
    hit = _FOREIGN_TOKENS.search(text)
    assert hit is None, f"{where}: language-specific example {hit.group(0)!r}"


def _walk_schema_descriptions(schema, path="") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(schema, dict):
        if isinstance(schema.get("description"), str):
            out.append((path or "<root>", schema["description"]))
        for key, value in schema.items():
            out.extend(_walk_schema_descriptions(value, f"{path}.{key}" if path else key))
    elif isinstance(schema, list):
        for i, item in enumerate(schema):
            out.extend(_walk_schema_descriptions(item, f"{path}[{i}]"))
    return out


def test_runtime_guardrail_is_language_neutral():
    _assert_neutral(_RUNTIME_GUARDRAIL, "_RUNTIME_GUARDRAIL")


def test_identity_primer_is_language_neutral():
    class _Agent:
        name = "Assistant"
        description = ""
        id = uuid.uuid4()

    _assert_neutral(_build_agent_identity_primer(_Agent()), "identity primer")


def test_page_context_primer_is_language_neutral():
    text = _format_page_context({"path": "/transactions", "label": "Transactions"})
    assert text
    _assert_neutral(text, "page context primer")


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_tool_descriptions_are_language_neutral(name):
    spec = REGISTRY[name]
    _assert_neutral(spec.description, f"{name}.description")
    for path, desc in _walk_schema_descriptions(spec.parameters):
        _assert_neutral(desc, f"{name}.parameters.{path}")


def test_guardrail_names_the_preferred_language_rule():
    # The rule the model actually follows: reply in the stated preference.
    assert "preferred language" in _RUNTIME_GUARDRAIL


@pytest.mark.parametrize(
    ("code", "expected"),
    [("en", "English (en)"), ("pt-BR", "Brazilian Portuguese (pt-BR)"), ("es-MX", "Spanish (es-MX)"), ("xx", "xx")],
)
def test_language_label_spells_out_the_language(code, expected):
    assert _language_label(code) == expected
