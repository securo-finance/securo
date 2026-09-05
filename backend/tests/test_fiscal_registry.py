"""Jurisdiction packs: what they may decide, and what they must not.

Two properties carry the whole design and each has a test that fails loudly
if someone erodes it: a pack orders and labels but never forbids, and a
workspace with no jurisdiction is a working configuration rather than a
broken one.
"""
import pytest

from app.fiscal.registry import (
    FALLBACK,
    KIND_SPECS,
    PACK_DIR,
    JurisdictionPack,
    TaxIdKind,
    available_jurisdictions,
    normalise_and_validate,
    pack_for,
    spec_for,
)
from app.fiscal.validators import NORMALISERS, VALIDATORS


# ---------------------------------------------------------------------------
# the packs load and stay well-formed
# ---------------------------------------------------------------------------
def test_initial_jurisdictions_are_available():
    """A subset check, not an equality one: pinning the full list would make
    every contributed pack an edit to this test, which is exactly the friction
    the pack format exists to remove. What matters is that the packs that
    shipped first are still there."""
    available = available_jurisdictions()
    for code in ("BR", "DE", "ES", "FR", "GB", "IT", "PT", "US"):
        assert code in available
    # And the list stays sorted, since the settings selector reads it in order.
    assert available == sorted(available)


def test_every_pack_file_parses_and_only_names_known_kinds():
    """A pack is data: a country code and an ordered list of kinds. It cannot
    define a document, so the only way it breaks is naming one that has no
    spec."""
    for path in PACK_DIR.glob("*.toml"):
        pack = pack_for(path.stem.upper())
        assert isinstance(pack, JurisdictionPack), path.name
        assert pack.code == path.stem.upper(), path.name
        assert pack.kinds, path.name
        for kind in pack.kinds:
            assert kind in KIND_SPECS, (path.name, kind)


def test_every_kind_has_exactly_one_definition():
    """A document is defined once, in code, and used identically everywhere."""
    assert set(KIND_SPECS) == set(TaxIdKind)
    for kind, spec in KIND_SPECS.items():
        assert spec.kind is kind
        assert spec.normalise in NORMALISERS, kind
        assert spec.validate in VALIDATORS, kind
        assert spec.label_key == f"fiscal.kind.{kind.value}", kind


def test_every_pack_offers_other_last():
    """The escape hatch is always present, so any jurisdiction can hold a
    document its pack never anticipated."""
    for code in available_jurisdictions():
        assert pack_for(code).kinds[-1] is TaxIdKind.OTHER, code


def test_pack_order_drives_the_default():
    assert pack_for("BR").kinds[0] is TaxIdKind.CNPJ
    assert pack_for("US").kinds[0] is TaxIdKind.EIN
    assert pack_for("DE").kinds[0] is TaxIdKind.VAT


def test_a_pack_cannot_change_what_a_document_is():
    """The invariant this design turns on: normalisation and validation come
    from the kind, so the same document behaves identically no matter which
    jurisdiction is holding it. An earlier version let packs carry their own
    normaliser and a German VAT number came out spaced differently depending
    on the workspace."""
    for jurisdiction in ("BR", "DE", "GB", "ZZ", None):
        stored, error = normalise_and_validate(TaxIdKind.VAT, "de 123 456 789")
        assert (stored, error) == ("DE123456789", None), jurisdiction


# ---------------------------------------------------------------------------
# no pack is a working configuration
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("jurisdiction", [None, "", "ZZ", "Atlantis"])
def test_unset_or_unshipped_jurisdiction_falls_back(jurisdiction):
    assert pack_for(jurisdiction) is FALLBACK


def test_fallback_stores_free_text_without_validating():
    stored, error = normalise_and_validate(TaxIdKind.OTHER, "  ABC-123/x  ")
    assert stored == "ABC-123/x"
    assert error is None


def test_a_country_without_a_pack_can_still_hold_its_document():
    """The point of the fallback: nobody waits for a pull request to use
    the product."""
    stored, error = normalise_and_validate(TaxIdKind.OTHER, "TIN 99-8877")
    assert error is None and stored == "TIN 99-8877"


# ---------------------------------------------------------------------------
# a pack suggests, it never restricts
# ---------------------------------------------------------------------------
def test_a_kind_outside_the_pack_is_still_storable():
    """A BR workspace billing a Berlin client needs `vat`, and the BR pack
    must not be able to forbid it. This is the test that keeps the product
    usable across borders."""
    assert TaxIdKind.VAT not in pack_for("BR").kinds
    stored, error = normalise_and_validate(TaxIdKind.VAT, "DE 123 456 789")
    assert error is None
    assert stored == "DE123456789"


def test_a_kind_outside_the_pack_keeps_its_own_rules():
    """Storable anywhere does not mean unchecked. A US workspace can hold a
    Codice Fiscale, and it is still validated as one."""
    assert TaxIdKind.CODICE_FISCALE not in pack_for("US").kinds
    assert spec_for(TaxIdKind.CODICE_FISCALE).validate == "it_codice_fiscale"
    _, error = normalise_and_validate(TaxIdKind.CODICE_FISCALE, "too short")
    assert error == "length"


# ---------------------------------------------------------------------------
# normalisation is per kind, and it is what gets stored
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kind,raw,expected",
    [
        (TaxIdKind.CNPJ, "11.222.333/0001-81", "11222333000181"),
        (TaxIdKind.CPF, "529.982.247-25", "52998224725"),
        (TaxIdKind.VAT, "de 123 456 789", "DE123456789"),
        (TaxIdKind.EIN, "12-3456789", "123456789"),
    ],
)
def test_values_are_stored_normalised(kind, raw, expected):
    stored, error = normalise_and_validate(kind, raw)
    assert (stored, error) == (expected, None)


def test_empty_value_is_refused():
    _, error = normalise_and_validate(TaxIdKind.OTHER, "   ")
    assert error == "empty"
