"""Named validators and normalisers for fiscal document values.

A jurisdiction pack never carries code. It names a validator, and the name
resolves here. That keeps a pack reviewable as data, keeps it safe to load
from outside the repo one day, and keeps validation on the server where it
belongs: the mask in the browser is a convenience, never the check.

Two rules hold for everything in this file:

  - **Nothing here touches the network.** Confirming a VAT number against
    VIES would tell a third party who this workspace bills. A format check
    that runs locally is worth less and costs nothing.
  - **A validator only rejects what is certainly wrong.** These documents
    end up on invoices, so catching a typo is worth it; guessing at a
    format that varies by state or by year is not. Where the rule is not
    globally defined, the validator is `none` and says so.
"""
from collections.abc import Callable

# ---------------------------------------------------------------------------
# normalisation — what gets stored
# ---------------------------------------------------------------------------
def normalise_digits(value: str) -> str:
    """Keep digits only. For documents that are numbers wearing punctuation."""
    return "".join(c for c in value if c.isdigit())


def normalise_upper_alnum(value: str) -> str:
    """Upper-case, drop separators. For VAT-shaped ids with a country prefix."""
    return "".join(c for c in value.upper() if c.isalnum())


def normalise_upper_compact(value: str) -> str:
    """Upper-case, drop separators, keep every other character.

    For documents where a symbol is part of the value: a Mexican RFC may
    contain `&` and `Ñ`, and `upper_alnum` would silently delete the `&`,
    turning a valid document into a different one.
    """
    return "".join(c for c in value.upper() if not c.isspace() and c not in "-./")


def normalise_trim(value: str) -> str:
    """Collapse surrounding whitespace and nothing else."""
    return value.strip()


NORMALISERS: dict[str, Callable[[str], str]] = {
    "digits": normalise_digits,
    "upper_alnum": normalise_upper_alnum,
    "upper_compact": normalise_upper_compact,
    "trim": normalise_trim,
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _mod11_check(digits: str, weights: list[int]) -> int:
    """Weighted mod-11 check digit, the shape most BR/PT documents use."""
    total = sum(int(d) * w for d, w in zip(digits, weights))
    remainder = total % 11
    return 0 if remainder < 2 else 11 - remainder


def _weighted_sum(digits: str, weights: list[int]) -> int:
    return sum(int(d) * w for d, w in zip(digits, weights))


def _digits_len(value: str, *lengths: int) -> str | None:
    """The check shared by every document whose only certain rule is its size."""
    if not value.isdigit():
        return "invalid"
    return None if len(value) in lengths else "length"


def _luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


# ---------------------------------------------------------------------------
# validators — return None when valid, else a machine-readable reason
# ---------------------------------------------------------------------------
def validate_none(value: str) -> None:
    """Accept anything non-empty.

    Used by `other` and by every document whose format is not globally
    defined. This is what lets a jurisdiction Securo has never shipped
    store a real document today.
    """
    return None


def validate_br_cpf(value: str) -> str | None:
    if len(value) != 11:
        return "length"
    # Repdigits pass the check-digit maths but are never issued.
    if value == value[0] * 11:
        return "invalid"
    first = _mod11_check(value[:9], list(range(10, 1, -1)))
    second = _mod11_check(value[:10], list(range(11, 1, -1)))
    if value[9] != str(first) or value[10] != str(second):
        return "check_digit"
    return None


def validate_br_cnpj(value: str) -> str | None:
    if len(value) != 14:
        return "length"
    if value == value[0] * 14:
        return "invalid"
    first = _mod11_check(value[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    second = _mod11_check(value[:13], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    if value[12] != str(first) or value[13] != str(second):
        return "check_digit"
    return None


def validate_pt_nif(value: str) -> str | None:
    if len(value) != 9:
        return "length"
    check = _mod11_check(value[:8], list(range(9, 1, -1)))
    # PT collapses both mod-11 remainders to 0 rather than to 0 and 1.
    expected = 0 if check > 9 else check
    if value[8] != str(expected):
        return "check_digit"
    return None


def validate_es_nif(value: str) -> str | None:
    """DNI/NIE shape: eight digits plus a check letter."""
    if len(value) != 9:
        return "length"
    body, letter = value[:8], value[8]
    # NIE starts with X/Y/Z, which stands in for a leading 0/1/2.
    if body[0] in "XYZ":
        body = str("XYZ".index(body[0])) + body[1:]
    if not body.isdigit():
        return "invalid"
    if letter != "TRWAGMYFPDXBNJZSQVHLCKE"[int(body) % 23]:
        return "check_digit"
    return None


def validate_it_partita_iva(value: str) -> str | None:
    if len(value) != 11:
        return "length"
    if not value.isdigit():
        return "invalid"
    return None if _luhn_ok(value) else "check_digit"


def validate_it_codice_fiscale(value: str) -> str | None:
    """Sixteen alphanumerics. The full check character needs a birth
    registry, so only the shape is enforced."""
    if len(value) != 16:
        return "length"
    return None if value.isalnum() else "invalid"


def validate_fr_siret(value: str) -> str | None:
    if len(value) != 14:
        return "length"
    if not value.isdigit():
        return "invalid"
    return None if _luhn_ok(value) else "check_digit"


def validate_de_steuernummer(value: str) -> str | None:
    """Ten or eleven digits. The state-specific layouts differ and the
    federal scheme is being rolled out, so length is all that is certain."""
    if not value.isdigit():
        return "invalid"
    return None if len(value) in (10, 11) else "length"


def validate_eu_vat_format(value: str) -> str | None:
    """Two-letter country prefix plus 2 to 13 alphanumerics.

    Format only. Each member state has its own check rule, and confirming
    the number really exists means calling VIES, which would disclose the
    workspace's client list to a third party.

    Also covers UK numbers, which kept the same shape after Brexit.
    """
    if len(value) < 4 or len(value) > 15:
        return "length"
    if not value[:2].isalpha():
        return "prefix"
    if not value[2:].isalnum():
        return "invalid"
    return None


def validate_us_ein(value: str) -> str | None:
    if not value.isdigit():
        return "invalid"
    return None if len(value) == 9 else "length"


# ---------------------------------------------------------------------------
# Latin America
# ---------------------------------------------------------------------------
def validate_ar_cuit(value: str) -> str | None:
    if len(value) != 11:
        return "length"
    check = 11 - (_weighted_sum(value[:10], [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]) % 11)
    # AR folds the two overflow remainders instead of rejecting them.
    expected = {11: 0, 10: 9}.get(check, check)
    return None if value[10] == str(expected) else "check_digit"


def validate_cl_rut(value: str) -> str | None:
    """Seven or eight digits plus a check character, which may be K."""
    if len(value) not in (8, 9):
        return "length"
    body, check = value[:-1], value[-1]
    if not body.isdigit():
        return "invalid"
    # Weights cycle 2..7 from the rightmost digit of the body.
    total = sum(int(d) * (2 + i % 6) for i, d in enumerate(reversed(body)))
    remainder = 11 - (total % 11)
    expected = {11: "0", 10: "K"}.get(remainder, str(remainder))
    return None if check == expected else "check_digit"


def validate_co_nit(value: str) -> str | None:
    """Nine digits, optionally followed by the verification digit.

    The DV is checked only when it is present: writing a NIT without it is
    common and is not an error.
    """
    if len(value) not in (9, 10):
        return "length"
    if not value.isdigit():
        return "invalid"
    if len(value) == 9:
        return None
    weights = [41, 37, 29, 23, 19, 17, 13, 7, 3]
    remainder = _weighted_sum(value[:9], weights) % 11
    expected = remainder if remainder < 2 else 11 - remainder
    return None if value[9] == str(expected) else "check_digit"


def validate_pe_ruc(value: str) -> str | None:
    if len(value) != 11:
        return "length"
    total = _weighted_sum(value[:10], [5, 4, 3, 2, 7, 6, 5, 4, 3, 2])
    expected = (11 - (total % 11)) % 10
    return None if value[10] == str(expected) else "check_digit"


def validate_uy_rut(value: str) -> str | None:
    """Twelve digits. The check rule is not published, so size is the check."""
    return _digits_len(value, 12)


def validate_cr_cedula(value: str) -> str | None:
    """Nine digits for a person, ten for a company. No published check digit."""
    return _digits_len(value, 9, 10)


def validate_do_rnc(value: str) -> str | None:
    """Nine digits for a company, eleven for a person's cédula."""
    return _digits_len(value, 9, 11)


# ---------------------------------------------------------------------------
# Europe outside the VAT-only packs
# ---------------------------------------------------------------------------
def validate_pl_nip(value: str) -> str | None:
    if len(value) != 10:
        return "length"
    if not value.isdigit():
        return "invalid"
    remainder = _weighted_sum(value[:9], [6, 5, 7, 2, 3, 4, 5, 6, 7]) % 11
    # A remainder of 10 cannot be expressed in one digit, so no such NIP exists.
    if remainder == 10:
        return "check_digit"
    return None if value[9] == str(remainder) else "check_digit"


def validate_se_orgnr(value: str) -> str | None:
    if len(value) != 10:
        return "length"
    if not value.isdigit():
        return "invalid"
    return None if _luhn_ok(value) else "check_digit"


def validate_dk_cvr(value: str) -> str | None:
    if len(value) != 8:
        return "length"
    if not value.isdigit():
        return "invalid"
    total = _weighted_sum(value, [2, 7, 6, 5, 4, 3, 2, 1])
    return None if total % 11 == 0 else "check_digit"


def validate_no_orgnr(value: str) -> str | None:
    if len(value) != 9:
        return "length"
    if not value.isdigit():
        return "invalid"
    remainder = _weighted_sum(value[:8], [3, 2, 7, 6, 5, 4, 3, 2]) % 11
    if remainder == 1:
        return "check_digit"
    expected = 0 if remainder == 0 else 11 - remainder
    return None if value[8] == str(expected) else "check_digit"


def validate_cz_ico(value: str) -> str | None:
    if len(value) != 8:
        return "length"
    if not value.isdigit():
        return "invalid"
    remainder = _weighted_sum(value[:7], [8, 7, 6, 5, 4, 3, 2]) % 11
    expected = {0: 1, 1: 0}.get(remainder, 11 - remainder)
    return None if value[7] == str(expected) else "check_digit"


def validate_hu_adoszam(value: str) -> str | None:
    """Eight digits for the core number, eleven with the VAT and county
    suffixes. The published check rule covers only some issuing years."""
    return _digits_len(value, 8, 11)


def validate_ro_cui(value: str) -> str | None:
    if not value.isdigit():
        return "invalid"
    if not 2 <= len(value) <= 10:
        return "length"
    body, check = value[:-1], value[-1]
    key = "753217532"[-len(body):]
    expected = (_weighted_sum(body, [int(k) for k in key]) * 10) % 11 % 10
    return None if check == str(expected) else "check_digit"


def validate_ch_uid(value: str) -> str | None:
    """CHE plus nine digits. The prefix is optional on input."""
    body = value[3:] if value.startswith("CHE") else value
    if len(body) != 9:
        return "length"
    if not body.isdigit():
        return "invalid"
    check = 11 - (_weighted_sum(body[:8], [5, 4, 3, 2, 7, 6, 5, 4]) % 11)
    if check == 10:
        return "check_digit"
    expected = 0 if check == 11 else check
    return None if body[8] == str(expected) else "check_digit"


def validate_ru_inn(value: str) -> str | None:
    """Ten digits for an organisation, twelve for a person."""
    if not value.isdigit():
        return "invalid"
    if len(value) == 10:
        expected = _weighted_sum(value[:9], [2, 4, 10, 3, 5, 9, 4, 6, 8]) % 11 % 10
        return None if value[9] == str(expected) else "check_digit"
    if len(value) == 12:
        first = _weighted_sum(value[:10], [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) % 11 % 10
        second = _weighted_sum(value[:11], [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) % 11 % 10
        if value[10] != str(first) or value[11] != str(second):
            return "check_digit"
        return None
    return "length"


def validate_ua_edrpou(value: str) -> str | None:
    """Eight digits. The check rule differs by registration range, so size
    is all that is certain."""
    return _digits_len(value, 8)


# ---------------------------------------------------------------------------
# Rest of the world
# ---------------------------------------------------------------------------
def validate_ca_bn(value: str) -> str | None:
    """Nine digits. The CRA does not publish its check rule, so rejecting on
    one would risk refusing a real Business Number."""
    return _digits_len(value, 9)


def validate_au_abn(value: str) -> str | None:
    if len(value) != 11:
        return "length"
    if not value.isdigit():
        return "invalid"
    weights = [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19]
    # The published algorithm subtracts one from the leading digit first.
    total = (int(value[0]) - 1) * weights[0] + _weighted_sum(value[1:], weights[1:])
    return None if total % 89 == 0 else "check_digit"


def validate_nz_nzbn(value: str) -> str | None:
    """Thirteen digits, GS1-issued. The check rule is not public."""
    return _digits_len(value, 13)


def validate_jp_corporate(value: str) -> str | None:
    """Thirteen digits. The leading check digit's rule varies by issuing
    batch, so only the size is enforced."""
    return _digits_len(value, 13)


def validate_ph_tin(value: str) -> str | None:
    """Nine digits, or twelve to fifteen with the branch code."""
    if not value.isdigit():
        return "invalid"
    return None if 9 <= len(value) <= 15 else "length"


def validate_id_npwp(value: str) -> str | None:
    """Fifteen digits, or sixteen since the NIK-based migration."""
    return _digits_len(value, 15, 16)


def validate_vn_mst(value: str) -> str | None:
    """Ten digits, or thirteen with the three-digit branch suffix.

    The tenth digit is a check digit, but reissued and legacy codes are in
    circulation that do not satisfy the published weighting, so refusing on
    it would reject real tax codes.
    """
    return _digits_len(value, 10, 13)


def validate_mx_rfc(value: str) -> str | None:
    """Twelve characters for a company, thirteen for a person.

    The trailing homoclave is assigned by the SAT and cannot be recomputed,
    so the shape is the only available check.
    """
    if len(value) not in (12, 13):
        return "length"
    letters = 3 if len(value) == 12 else 4
    if not all(c.isalpha() or c in "&Ñ" for c in value[:letters]):
        return "invalid"
    if not value[letters : letters + 6].isdigit():
        return "invalid"
    return None if value[letters + 6 :].isalnum() else "invalid"


def validate_in_gstin(value: str) -> str | None:
    """Two state digits, a PAN, an entity digit and two trailing characters."""
    if len(value) != 15:
        return "length"
    if not value[:2].isdigit():
        return "invalid"
    return "invalid" if validate_in_pan(value[2:12]) else None


def validate_in_pan(value: str) -> str | None:
    if len(value) != 10:
        return "length"
    if not (value[:5].isalpha() and value[5:9].isdigit() and value[9].isalpha()):
        return "invalid"
    return None


def validate_sg_uen(value: str) -> str | None:
    """A UEN in one of the three shapes ACRA issues, each ending in a check
    letter:

      - business:      eight digits plus the letter (53012345A)
      - local company: year, five digits, the letter (201012345A)
      - other entity:  T/S/R, two year digits, two letters, four digits, letter

    The check letter's algorithm is not published, so only the shape is
    enforced: refusing on a reverse-engineered rule would reject real numbers.
    """
    if len(value) not in (9, 10):
        return "length"
    if not value[-1].isalpha():
        return "invalid"
    body = value[:-1]
    if len(value) == 9:
        return None if body.isdigit() else "invalid"
    if body.isdigit():
        return None
    if value[0] in "TSR" and body[1:3].isdigit() and body[3:5].isalpha() and body[5:].isdigit():
        return None
    return "invalid"

def validate_az_voen(value: str) -> str | None:
    """Ten digits. Check for a valid 10-digit format without checksum validation."""
    return _digits_len(value, 10)


def validate_cn_uscc(value: str) -> str | None:
    """Eighteen characters from a restricted alphabet: I, O, S, V and Z are
    excluded to keep the code unambiguous when read aloud."""
    if len(value) != 18:
        return "length"
    allowed = set("0123456789ABCDEFGHJKLMNPQRTUWXY")
    return None if set(value) <= allowed else "invalid"


VALIDATORS: dict[str, Callable[[str], str | None]] = {
    "none": validate_none,
    "br_cpf": validate_br_cpf,
    "br_cnpj": validate_br_cnpj,
    "pt_nif": validate_pt_nif,
    "es_nif": validate_es_nif,
    "it_partita_iva": validate_it_partita_iva,
    "it_codice_fiscale": validate_it_codice_fiscale,
    "fr_siret": validate_fr_siret,
    "de_steuernummer": validate_de_steuernummer,
    "eu_vat_format": validate_eu_vat_format,
    "us_ein": validate_us_ein,
    "ar_cuit": validate_ar_cuit,
    "cl_rut": validate_cl_rut,
    "co_nit": validate_co_nit,
    "pe_ruc": validate_pe_ruc,
    "uy_rut": validate_uy_rut,
    "cr_cedula": validate_cr_cedula,
    "do_rnc": validate_do_rnc,
    "mx_rfc": validate_mx_rfc,
    "pl_nip": validate_pl_nip,
    "se_orgnr": validate_se_orgnr,
    "dk_cvr": validate_dk_cvr,
    "no_orgnr": validate_no_orgnr,
    "cz_ico": validate_cz_ico,
    "hu_adoszam": validate_hu_adoszam,
    "ro_cui": validate_ro_cui,
    "ch_uid": validate_ch_uid,
    "ru_inn": validate_ru_inn,
    "ua_edrpou": validate_ua_edrpou,
    "ca_bn": validate_ca_bn,
    "au_abn": validate_au_abn,
    "nz_nzbn": validate_nz_nzbn,
    "jp_corporate": validate_jp_corporate,
    "ph_tin": validate_ph_tin,
    "id_npwp": validate_id_npwp,
    "vn_mst": validate_vn_mst,
    "in_gstin": validate_in_gstin,
    "in_pan": validate_in_pan,
    "cn_uscc": validate_cn_uscc,
    "sg_uen": validate_sg_uen,
    "az_voen": validate_az_voen,
}
