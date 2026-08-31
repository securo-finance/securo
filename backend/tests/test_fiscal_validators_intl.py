"""Check-digit validators, tested against documents that really exist.

Every case below is a public identifier of a well-known organisation, taken
from its own filings. That matters more than it looks: a check-digit routine
written from a prose description can be subtly wrong and still pass any test
built from numbers the same routine generated. Only a real document proves the
algorithm, and a wrong algorithm here silently refuses a real user's client.

Where a document's check rule is not published, or varies by issuing year or
state, the validator checks size and shape only. Those cases assert exactly
that, so nobody later mistakes the gap for an oversight.
"""
import pytest

from app.fiscal.registry import TaxIdKind, normalise_and_validate
from app.fiscal.validators import VALIDATORS

#: (validator, value, whose document it is)
REAL_DOCUMENTS = [
    ("ar_cuit", "30500003193", "Banco de la Nación Argentina"),
    ("ar_cuit", "30546689979", "YPF"),
    ("cl_rut", "970040005", "Banco de Chile"),
    ("cl_rut", "609100001", "Fisco de Chile"),
    ("co_nit", "8909039388", "Bancolombia, with the verification digit"),
    ("co_nit", "890903938", "Bancolombia, without it"),
    ("pe_ruc", "20131312955", "SUNAT"),
    ("pl_nip", "5260250274", "a Polish company"),
    ("se_orgnr", "5560360793", "Volvo"),
    ("dk_cvr", "61126228", "Danske Bank"),
    ("no_orgnr", "923609016", "Equinor"),
    ("cz_ico", "45274649", "ČEZ"),
    ("ch_uid", "CHE116281710", "Nestlé"),
    ("ch_uid", "116281710", "Nestlé, prefix omitted"),
    ("ru_inn", "7707083893", "Sberbank"),
    ("au_abn", "51824753556", "the Australian Taxation Office"),
    ("ro_cui", "14399840", "a Romanian company"),
]

#: Validators that verify a check digit, so a single altered digit must fail.
CHECKED = {
    "ar_cuit",
    "cl_rut",
    "pe_ruc",
    "pl_nip",
    "se_orgnr",
    "dk_cvr",
    "no_orgnr",
    "cz_ico",
    "ch_uid",
    "ru_inn",
    "au_abn",
    "ro_cui",
}


@pytest.mark.parametrize("validator,value,whose", REAL_DOCUMENTS)
def test_a_real_document_is_accepted(validator, value, whose):
    assert VALIDATORS[validator](value) is None, f"refused {whose}'s document"


@pytest.mark.parametrize(
    "validator,value,whose", [c for c in REAL_DOCUMENTS if c[0] in CHECKED]
)
def test_one_altered_digit_is_refused(validator, value, whose):
    """The point of a check digit: a typo must not pass."""
    altered = value[:-1] + ("1" if value[-1] != "1" else "2")
    assert VALIDATORS[validator](altered) == "check_digit"


def test_a_chilean_rut_check_character_may_be_k():
    """The reason `cl_rut` is stored as text and carries no mask."""
    assert VALIDATORS["cl_rut"]("11111112K") is None
    # The same body with a digit in place of the K is a different, wrong RUT.
    assert VALIDATORS["cl_rut"]("111111120") == "check_digit"


def test_a_colombian_nit_may_omit_its_verification_digit():
    """Quoting a NIT without the DV is normal practice, not an error."""
    assert VALIDATORS["co_nit"]("890903938") is None
    assert VALIDATORS["co_nit"]("8909039388") is None
    assert VALIDATORS["co_nit"]("8909039380") == "check_digit"


def test_a_russian_inn_has_two_lengths():
    """Ten digits for an organisation, twelve for a person."""
    assert VALIDATORS["ru_inn"]("7707083893") is None
    assert VALIDATORS["ru_inn"]("77070838931") == "length"


# ---------------------------------------------------------------------------
# shape-only validators: assert the gap deliberately
# ---------------------------------------------------------------------------
SHAPE_ONLY = [
    # validator, accepted, refused, why the check stops at the shape
    ("ca_bn", "123456789", "12345678", "the CRA does not publish its check rule"),
    ("nz_nzbn", "9429030000004", "942903000000", "the NZBN check rule is not public"),
    ("jp_corporate", "7000012050002", "700001205000", "the leading digit's rule varies"),
    ("uy_rut", "211003420011", "21100342001", "the DGI check rule is not published"),
    # Nine digits is a person's cédula and ten a company's, so both pass; the
    # refused case has to be shorter than either.
    ("cr_cedula", "3101002771", "31010027", "no published check digit"),
    ("do_rnc", "101010101", "10101010", "company and cédula lengths differ"),
    ("ua_edrpou", "00032129", "0003212", "the rule differs by registration range"),
    ("hu_adoszam", "12345678142", "1234567814", "the published rule covers some years"),
    ("id_npwp", "012345678901234", "01234567890123", "15 and 16 both circulate"),
    ("az_voen", "1234567890", "123456789", "the VÖEN check rule is not published"),
]


@pytest.mark.parametrize("validator,accepted,refused,why", SHAPE_ONLY)
def test_a_shape_only_validator_checks_size_and_nothing_more(
    validator, accepted, refused, why
):
    assert VALIDATORS[validator](accepted) is None, why
    assert VALIDATORS[validator](refused) == "length", why
    # And it does not pretend to verify a check digit.
    altered = accepted[:-1] + ("1" if accepted[-1] != "1" else "2")
    assert VALIDATORS[validator](altered) is None, (
        f"{validator} appears to check a digit it was documented not to: {why}"
    )


def test_a_mexican_rfc_keeps_its_ampersand():
    """`upper_alnum` would delete the `&`, storing a different document."""
    stored, error = normalise_and_validate(TaxIdKind.RFC, "ab&940707ie1")
    assert stored == "AB&940707IE1"
    assert error is None


def test_a_mexican_rfc_rejects_a_letter_where_the_date_goes():
    # O for 0 is the classic transcription slip in an RFC.
    assert VALIDATORS["mx_rfc"]("BBA94O707IE1") == "invalid"


def test_an_indian_gstin_must_contain_a_well_formed_pan():
    assert VALIDATORS["in_gstin"]("27AAACR5055K1Z2") is None
    # Digits where the PAN's leading letters belong.
    assert VALIDATORS["in_gstin"]("2712ACR5055K1Z2") == "invalid"
    assert VALIDATORS["in_pan"]("AAAC15055K") == "invalid"


def test_a_chinese_uscc_excludes_the_ambiguous_letters():
    """I, O, S, V and Z are left out so the code survives being read aloud."""
    assert VALIDATORS["cn_uscc"]("91310000MA1K35Y47B") is None
    assert VALIDATORS["cn_uscc"]("91310000MA1K35Y47I") == "invalid"
