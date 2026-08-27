"""The rule migration 077 uses to decide that a payee name *is* a document.

Worth a test of its own because the failure it guards against is silent and
wrong rather than loud: attaching somebody's CPF to a shop because the
shop's name happened to contain eleven digits that passed the check digits.
Check digits alone let that through roughly once in a hundred, so the
migration also requires the name to be nothing but a document, and that is
the part most likely to be "simplified" later by someone who reads only the
maths.

Loaded through importlib because a migration filename starts with a digit
and is not importable as a module path.
"""

import importlib.util
import pathlib

import pytest

_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "077_backfill_payee_tax_ids_from_original_name.py"
)
_spec = importlib.util.spec_from_file_location("migration_077", _PATH)
assert _spec is not None and _spec.loader is not None, f"cannot load {_PATH}"
migration_077 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_077)

classify = migration_077._classify

#: Fictitious but check-digit-valid, because the whole point is the maths.
CPF = "529.982.247-25"
CNPJ = "11.222.333/0001-81"


@pytest.mark.parametrize(
    "name,expected",
    [
        (CPF, ("cpf", "52998224725")),
        ("52998224725", ("cpf", "52998224725")),
        ("  529.982.247-25  ", ("cpf", "52998224725")),
        (CNPJ, ("cnpj", "11222333000181")),
        ("11222333000181", ("cnpj", "11222333000181")),
        ("11.222.333 0001-81", ("cnpj", "11222333000181")),  # bank writes / as a space
    ],
)
def test_a_bare_document_is_recognised(name, expected):
    assert classify(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        # An MEI trades under "<CNPJ root> <person's name>". The root is not a
        # CNPJ, and the name is not a document — this is the case that made
        # the letters rule necessary.
        "11.222.333 JOHN DOE SILVA",
        "JOHN DOE SILVA",
        "UBER *TRIP",
        "LOJA 52998224725 LTDA",  # a real CPF, but the name is not one
        "529.982.247-26",  # second check digit wrong
        "529.982.247-15",  # first check digit wrong
        "111.111.111-11",  # repdigits pass the maths and are never issued
        "11.111.111/1111-11",
        "1234567890",  # too short for either
        "529982247251",  # twelve digits: neither shape
        "",
        "   ",
        None,
    ],
)
def test_anything_else_is_refused(name):
    assert classify(name) is None
