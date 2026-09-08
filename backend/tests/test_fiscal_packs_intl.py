"""Invariants that must hold across every jurisdiction pack.

These are the tests that keep adding a country cheap. Each one fails loudly on
the mistake it describes, so a new pack is a two-line TOML file and not an
archaeology exercise.
"""
import pytest

from app.fiscal.registry import (
    KIND_SPECS,
    TaxIdKind,
    available_jurisdictions,
    normalise_and_validate,
    pack_for,
    spec_for,
)

ALL_JURISDICTIONS = available_jurisdictions()


def test_every_kind_has_a_spec():
    missing = [k.value for k in TaxIdKind if k not in KIND_SPECS]
    assert missing == []


def test_every_kind_is_offered_by_some_pack():
    """A kind no pack lists is unreachable in the picker, so it is dead code
    that still shows up in the API response."""
    offered = {kind for code in ALL_JURISDICTIONS for kind in pack_for(code).kinds}
    orphans = [k.value for k in TaxIdKind if k not in offered]
    assert orphans == []


@pytest.mark.parametrize("code", ALL_JURISDICTIONS)
def test_a_pack_offers_at_least_one_document_besides_the_escape_hatch(code):
    kinds = [k for k in pack_for(code).kinds if k is not TaxIdKind.OTHER]
    assert kinds, f"{code} offers nothing but `other`, so the pack adds nothing"


@pytest.mark.parametrize("code", ALL_JURISDICTIONS)
def test_every_pack_ends_with_the_escape_hatch(code):
    """So a document the pack never anticipated is always storable."""
    assert pack_for(code).kinds[-1] is TaxIdKind.OTHER


@pytest.mark.parametrize("kind", [k for k in TaxIdKind if KIND_SPECS[k].mask])
def test_a_mask_is_only_given_to_a_fixed_length_document(kind):
    """Masks truncate at the template's width when applied as the user types,
    so a variable-length document under a mask reads back mangled: a
    seven-digit Chilean RUT in an eight-digit mask becomes `12.345.67-8`.

    The check: feed the mask's own width in digits, and the value must survive
    validation. A validator that accepts a second, shorter length is the signal
    that this kind should not carry a mask at all.
    """
    spec = spec_for(kind)
    assert spec.mask is not None  # the parametrize filters to masked kinds
    slots = spec.mask.count("#")
    # A CH UID's mask supplies its CHE prefix, which the value carries too.
    filled = ("CHE" if kind is TaxIdKind.CH_UID else "") + "1" * slots
    _, error = normalise_and_validate(kind, filled)
    # Not asserting the digits pass the check maths, only that the *length*
    # the mask produces is the length the validator expects.
    assert error != "length", (
        f"{kind.value}'s mask yields {slots} digits, which its validator "
        f"refuses on length; it should carry no mask"
    )


@pytest.mark.parametrize("code", ALL_JURISDICTIONS)
def test_a_pack_cannot_change_what_a_document_is(code):
    """The load-bearing invariant: a jurisdiction chooses kinds, it never
    redefines one. The same CNPJ stored from any workspace normalises and
    validates identically."""
    stored, error = normalise_and_validate(TaxIdKind.CNPJ, "11.222.333/0001-81")
    assert stored == "11222333000181"
    assert error is None
    # And the pack's own primary document behaves the same regardless of who
    # asks: `normalise_and_validate` takes no jurisdiction at all.
    primary = pack_for(code).kinds[0]
    assert spec_for(primary) is KIND_SPECS[primary]


def test_an_unknown_jurisdiction_still_works():
    """A country nobody has contributed a pack for is usable today."""
    pack = pack_for("ZW")
    assert pack.kinds == (TaxIdKind.OTHER,)
    stored, error = normalise_and_validate(TaxIdKind.OTHER, "  BP/1234/2024  ")
    assert stored == "BP/1234/2024"
    assert error is None


def test_the_currencies_the_product_supports_have_a_pack():
    """The packs were chosen to cover the currencies already supported, so a
    workspace can bill in a currency and name the document that goes with it."""
    from app.api.currencies import CURRENCY_META

    # Currency to the country whose fiscal documents it implies. The euro and
    # the dollar are shared by many, so they are covered by their members'
    # own packs rather than mapped here.
    country_of = {
        "AZN": "AZ", "BRL": "BR", "GBP": "GB", "JPY": "JP", "CAD": "CA", "AUD": "AU",
        "CHF": "CH", "CNY": "CN", "ARS": "AR", "MXN": "MX", "CLP": "CL",
        "COP": "CO", "PEN": "PE", "UYU": "UY", "INR": "IN", "SEK": "SE",
        "DKK": "DK", "NOK": "NO", "PLN": "PL", "CZK": "CZ", "HUF": "HU",
        "RON": "RO", "CRC": "CR", "IDR": "ID", "DOP": "DO", "RUB": "RU",
        "GTQ": "GT", "PHP": "PH", "UAH": "UA", "NZD": "NZ", "VND": "VN", "SGD": "SG", "USD": "US",
    }
    uncovered = [
        code
        for currency, code in country_of.items()
        if currency in CURRENCY_META and code not in ALL_JURISDICTIONS
    ]
    assert uncovered == []
