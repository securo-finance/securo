"""The named validators a jurisdiction pack points at.

Real documents where a real one is safe to publish (the CNPJ/CPF values here
are the canonical test numbers), and the failure modes are separated so a
regression says which rule broke rather than just "invalid".
"""
import pytest

from app.fiscal import validators as v


# ---------------------------------------------------------------------------
# Brazil — the only ones with check digits worth enforcing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["11222333000181", "34028316000103"])
def test_valid_cnpj(value):
    assert v.validate_br_cnpj(value) is None


def test_cnpj_failure_modes():
    assert v.validate_br_cnpj("1122233300018") == "length"
    assert v.validate_br_cnpj("11222333000199") == "check_digit"
    # Repdigits satisfy the arithmetic but are never issued.
    assert v.validate_br_cnpj("11111111111111") == "invalid"


@pytest.mark.parametrize("value", ["52998224725", "11144477735"])
def test_valid_cpf(value):
    assert v.validate_br_cpf(value) is None


def test_cpf_failure_modes():
    assert v.validate_br_cpf("5299822472") == "length"
    assert v.validate_br_cpf("52998224724") == "check_digit"
    assert v.validate_br_cpf("00000000000") == "invalid"


def test_a_typo_in_one_digit_is_caught():
    """The reason check digits are here at all: these values end up on an
    invoice and in a contador export."""
    good = "11222333000181"
    for index in range(12):
        broken = good[:index] + str((int(good[index]) + 1) % 10) + good[index + 1 :]
        assert v.validate_br_cnpj(broken) is not None, broken


# ---------------------------------------------------------------------------
# Iberia
# ---------------------------------------------------------------------------
def test_pt_nif():
    assert v.validate_pt_nif("501442600") is None
    assert v.validate_pt_nif("501442601") == "check_digit"
    assert v.validate_pt_nif("50144260") == "length"


def test_es_nif_dni_and_nie():
    assert v.validate_es_nif("12345678Z") is None
    assert v.validate_es_nif("X1234567L") is None
    assert v.validate_es_nif("12345678A") == "check_digit"
    assert v.validate_es_nif("1234567Z") == "length"


# ---------------------------------------------------------------------------
# the rest are shape checks, and that is deliberate
# ---------------------------------------------------------------------------
def test_it_documents():
    assert v.validate_it_partita_iva("00743110157") is None
    assert v.validate_it_partita_iva("00743110158") == "check_digit"
    assert v.validate_it_codice_fiscale("RSSMRA85M01H501Z") is None
    assert v.validate_it_codice_fiscale("RSSMRA85M01H501") == "length"


def test_fr_siret_uses_luhn():
    assert v.validate_fr_siret("73282932000074") is None
    assert v.validate_fr_siret("73282932000075") == "check_digit"


def test_de_steuernummer_is_length_only():
    """The state layouts disagree and the federal scheme is mid-rollout, so
    anything stricter would reject real numbers."""
    assert v.validate_de_steuernummer("1234567890") is None
    assert v.validate_de_steuernummer("12345678901") is None
    assert v.validate_de_steuernummer("123456789") == "length"
    assert v.validate_de_steuernummer("12345abc90") == "invalid"


def test_eu_vat_is_format_only():
    """No VIES call: confirming the number exists would disclose the
    workspace's client list to a third party."""
    assert v.validate_eu_vat_format("DE123456789") is None
    assert v.validate_eu_vat_format("FRAB123456789") is None
    assert v.validate_eu_vat_format("123456789") == "prefix"
    assert v.validate_eu_vat_format("DE1") == "length"


def test_uk_numbers_use_the_same_shape_check():
    """GB shares the `vat` kind: after Brexit the scheme differs but the
    shape does not, and a separate validator would have been dead weight."""
    assert v.validate_eu_vat_format("GB123456789") is None
    assert v.validate_eu_vat_format("GB123456789012") is None


def test_us_ein():
    assert v.validate_us_ein("123456789") is None
    assert v.validate_us_ein("12345678") == "length"


def test_validate_none_accepts_anything():
    """Used by `other` and by documents whose format varies by state, such
    as the Brazilian Inscrição Estadual."""
    for value in ("x", "ISENTO", "123.456.789/xyz"):
        assert v.validate_none(value) is None


# ---------------------------------------------------------------------------
# registry integrity
# ---------------------------------------------------------------------------
def test_every_validator_is_reachable_by_name():
    for name, fn in v.VALIDATORS.items():
        assert callable(fn), name
        # A validator returns None or a short reason, never raises on a
        # plausible input.
        assert fn("123") is None or isinstance(fn("123"), str), name


def test_normalisers_are_idempotent():
    for name, fn in v.NORMALISERS.items():
        for value in ("  a1-b2 ", "11.222.333/0001-81"):
            assert fn(fn(value)) == fn(value), name
