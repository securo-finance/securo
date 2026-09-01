"""Jurisdiction packs: which documents a country asks for, in what order.

The split that makes this work, and the one thing to preserve if this file is
ever refactored:

  - **A kind defines the document.** How to normalise it, how to validate it,
    how to mask it, what to call it. One definition, in code, used everywhere.
    A CNPJ is fourteen digits with a check digit whether the workspace is set
    to Brazil, to Germany, or to nothing at all, so a stored value stays
    comparable and a document typed in one workspace means the same in the
    next.
  - **A jurisdiction chooses from them.** A pack is a country code and an
    ordered list of kinds, nothing more. It cannot weaken a validator, cannot
    rename a document, and cannot make the same kind behave differently than
    it does elsewhere.

That split is deliberate and was arrived at the hard way: an earlier version
let packs carry their own `normalise`, and a German VAT number stored from a
Brazilian workspace came out spaced differently than the same number stored
from a German one.

Two further properties:

  - **A pack suggests; it never restricts.** It sets what a workspace is
    *offered*. Every kind stays storable in every workspace, because the
    counterparty's country is not the workspace's: a Brazilian consultancy
    billing a Berlin client needs `vat`, and the BR pack must not forbid it.
  - **No pack is a working configuration.** An unset or unknown
    `tax_jurisdiction` resolves to FALLBACK: free text under `other`, no
    mask, no validation. Packs are enhancement, never a prerequisite, so a
    country nobody has contributed yet is usable today.

Adding a country is one TOML file. Adding a *document* is a new `TaxIdKind`
plus one entry in KIND_SPECS, and a named validator if its format is
checkable. Both are pull requests, which is the point: a document that no
code understands is a document no export can use.
"""
import tomllib
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path

from app.fiscal.validators import NORMALISERS, VALIDATORS

PACK_DIR = Path(__file__).parent / "jurisdictions"


class TaxIdKind(str, Enum):
    """Every document the product can store.

    Closed on purpose. A free-form string would make the stored value
    uninterpretable, which is the failure this whole design exists to avoid.

    A kind names **one specific document**, never a category. Portugal's and
    Spain's national numbers are both called NIF and are different documents
    with different check rules, so they are different kinds. `vat` is the one
    genuinely shared scheme: the country prefix is part of the value.
    """

    # Brazil
    CNPJ = "cnpj"
    CPF = "cpf"
    IE = "ie"
    IM = "im"
    # Iberia
    PT_NIF = "pt_nif"
    ES_NIF = "es_nif"
    # European VAT, including the UK, whose numbers still fit the shape
    VAT = "vat"
    # Germany's domestic number, distinct from its USt-IdNr
    STEUERNUMMER = "steuernummer"
    # Italy
    PARTITA_IVA = "partita_iva"
    CODICE_FISCALE = "codice_fiscale"
    SDI = "sdi"
    # France
    SIRET = "siret"
    # United States
    EIN = "ein"
    # Rest of Europe. Prefixed where the acronym is not unique: SE and NO both
    # call theirs an organisasjons/organisationsnummer, and they check
    # differently.
    NIP = "nip"
    SE_ORGNR = "se_orgnr"
    NO_ORGNR = "no_orgnr"
    CVR = "cvr"
    ICO = "ico"
    ADOSZAM = "adoszam"
    RO_CUI = "ro_cui"
    CH_UID = "ch_uid"
    INN = "inn"
    EDRPOU = "edrpou"
    # Latin America
    CUIT = "cuit"
    RFC = "rfc"
    CL_RUT = "cl_rut"
    UY_RUT = "uy_rut"
    CO_NIT = "co_nit"
    GT_NIT = "gt_nit"
    PE_RUC = "pe_ruc"
    CR_CEDULA = "cr_cedula"
    RNC = "rnc"
    # Rest of the world
    CA_BN = "ca_bn"
    ABN = "abn"
    NZBN = "nzbn"
    JP_CORPORATE = "jp_corporate"
    GSTIN = "gstin"
    PAN = "pan"
    NPWP = "npwp"
    PH_TIN = "ph_tin"
    VN_MST = "vn_mst"
    SG_UEN = "sg_uen"
    USCC = "uscc"
    AZ_VOEN = "az_voen"
    # The escape hatch. Always offered, never validated.
    OTHER = "other"


@dataclass(frozen=True)
class KindSpec:
    """What one document is, independent of who asks for it."""

    kind: TaxIdKind
    label_key: str
    # Presentation only: the frontend applies it, the backend ignores it.
    mask: str | None = None
    normalise: str = "trim"
    validate: str = "none"

    def normalised(self, value: str) -> str:
        return NORMALISERS[self.normalise](value)

    def error_for(self, value: str) -> str | None:
        """None when acceptable, else a short machine-readable reason."""
        if not value:
            return "empty"
        return VALIDATORS[self.validate](value)


def _spec(
    kind: TaxIdKind, normalise: str = "trim", validate: str = "none", mask: str | None = None
) -> KindSpec:
    return KindSpec(
        kind=kind,
        label_key=f"fiscal.kind.{kind.value}",
        mask=mask,
        normalise=normalise,
        validate=validate,
    )


#: One definition per document. `ie` and `im` are unvalidated on purpose: the
#: twenty-seven Brazilian state schemes disagree with each other, and "ISENTO"
#: is a legitimate value.
#:
#: A mask is only given to a document of **fixed** length. Masks are applied as
#: the user types and truncate at the template's width, so putting one on a
#: variable-length document mangles the short form of it: a seven-digit Chilean
#: RUT under an eight-digit mask reads back as `12.345.67-8`. Those kinds are
#: shown as typed instead, which is correct if plainer.
KIND_SPECS: dict[TaxIdKind, KindSpec] = {
    TaxIdKind.CNPJ: _spec(TaxIdKind.CNPJ, "digits", "br_cnpj", "##.###.###/####-##"),
    TaxIdKind.CPF: _spec(TaxIdKind.CPF, "digits", "br_cpf", "###.###.###-##"),
    TaxIdKind.IE: _spec(TaxIdKind.IE, "upper_alnum"),
    TaxIdKind.IM: _spec(TaxIdKind.IM, "upper_alnum"),
    TaxIdKind.PT_NIF: _spec(TaxIdKind.PT_NIF, "digits", "pt_nif", "### ### ###"),
    TaxIdKind.ES_NIF: _spec(TaxIdKind.ES_NIF, "upper_alnum", "es_nif"),
    TaxIdKind.VAT: _spec(TaxIdKind.VAT, "upper_alnum", "eu_vat_format"),
    TaxIdKind.STEUERNUMMER: _spec(TaxIdKind.STEUERNUMMER, "digits", "de_steuernummer"),
    TaxIdKind.PARTITA_IVA: _spec(TaxIdKind.PARTITA_IVA, "digits", "it_partita_iva"),
    TaxIdKind.CODICE_FISCALE: _spec(TaxIdKind.CODICE_FISCALE, "upper_alnum", "it_codice_fiscale"),
    TaxIdKind.SDI: _spec(TaxIdKind.SDI, "upper_alnum"),
    TaxIdKind.SIRET: _spec(TaxIdKind.SIRET, "digits", "fr_siret", "### ### ### #####"),
    TaxIdKind.EIN: _spec(TaxIdKind.EIN, "digits", "us_ein", "##-#######"),
    # Rest of Europe
    TaxIdKind.NIP: _spec(TaxIdKind.NIP, "digits", "pl_nip", "###-###-##-##"),
    TaxIdKind.SE_ORGNR: _spec(TaxIdKind.SE_ORGNR, "digits", "se_orgnr", "######-####"),
    TaxIdKind.NO_ORGNR: _spec(TaxIdKind.NO_ORGNR, "digits", "no_orgnr", "### ### ###"),
    TaxIdKind.CVR: _spec(TaxIdKind.CVR, "digits", "dk_cvr"),
    TaxIdKind.ICO: _spec(TaxIdKind.ICO, "digits", "cz_ico"),
    TaxIdKind.ADOSZAM: _spec(TaxIdKind.ADOSZAM, "digits", "hu_adoszam"),
    TaxIdKind.RO_CUI: _spec(TaxIdKind.RO_CUI, "digits", "ro_cui"),
    # The mask supplies the CHE prefix, so it reads back whole even when only
    # the nine digits were typed.
    TaxIdKind.CH_UID: _spec(TaxIdKind.CH_UID, "upper_alnum", "ch_uid", "CHE-###.###.###"),
    TaxIdKind.INN: _spec(TaxIdKind.INN, "digits", "ru_inn"),
    TaxIdKind.EDRPOU: _spec(TaxIdKind.EDRPOU, "digits", "ua_edrpou"),
    # Latin America
    TaxIdKind.CUIT: _spec(TaxIdKind.CUIT, "digits", "ar_cuit", "##-########-#"),
    # `upper_compact` rather than `upper_alnum`: an RFC may contain `&`, which
    # dropping would turn a valid document into a different one.
    TaxIdKind.RFC: _spec(TaxIdKind.RFC, "upper_compact", "mx_rfc"),
    TaxIdKind.CL_RUT: _spec(TaxIdKind.CL_RUT, "upper_alnum", "cl_rut"),
    TaxIdKind.UY_RUT: _spec(TaxIdKind.UY_RUT, "digits", "uy_rut"),
    TaxIdKind.CO_NIT: _spec(TaxIdKind.CO_NIT, "digits", "co_nit"),
    TaxIdKind.GT_NIT: _spec(TaxIdKind.GT_NIT, "upper_alnum"),
    TaxIdKind.PE_RUC: _spec(TaxIdKind.PE_RUC, "digits", "pe_ruc"),
    TaxIdKind.CR_CEDULA: _spec(TaxIdKind.CR_CEDULA, "digits", "cr_cedula"),
    TaxIdKind.RNC: _spec(TaxIdKind.RNC, "digits", "do_rnc"),
    # Rest of the world
    TaxIdKind.CA_BN: _spec(TaxIdKind.CA_BN, "digits", "ca_bn", "### ### ###"),
    TaxIdKind.ABN: _spec(TaxIdKind.ABN, "digits", "au_abn", "## ### ### ###"),
    TaxIdKind.NZBN: _spec(TaxIdKind.NZBN, "digits", "nz_nzbn"),
    TaxIdKind.JP_CORPORATE: _spec(TaxIdKind.JP_CORPORATE, "digits", "jp_corporate"),
    TaxIdKind.GSTIN: _spec(TaxIdKind.GSTIN, "upper_alnum", "in_gstin"),
    TaxIdKind.PAN: _spec(TaxIdKind.PAN, "upper_alnum", "in_pan"),
    TaxIdKind.NPWP: _spec(TaxIdKind.NPWP, "digits", "id_npwp"),
    TaxIdKind.PH_TIN: _spec(TaxIdKind.PH_TIN, "digits", "ph_tin"),
    TaxIdKind.VN_MST: _spec(TaxIdKind.VN_MST, "digits", "vn_mst"),
    TaxIdKind.SG_UEN: _spec(TaxIdKind.SG_UEN, "upper_alnum", "sg_uen"),
    TaxIdKind.AZ_VOEN: _spec(TaxIdKind.AZ_VOEN, "digits", "az_voen", "##########"),
    TaxIdKind.USCC: _spec(TaxIdKind.USCC, "upper_alnum", "cn_uscc"),
    TaxIdKind.OTHER: _spec(TaxIdKind.OTHER, "trim"),
}


def spec_for(kind: TaxIdKind) -> KindSpec:
    """The definition of a document. Never jurisdiction-dependent."""
    return KIND_SPECS[kind]


@dataclass(frozen=True)
class JurisdictionPack:
    code: str
    #: Ordered. The first is what the UI defaults to; `other` is always last.
    kinds: tuple[TaxIdKind, ...]


#: What a deployment with no jurisdiction set gets. Empty of opinions rather
#: than quietly defaulting to somebody's country.
FALLBACK = JurisdictionPack(code="", kinds=(TaxIdKind.OTHER,))


def _load_pack(path: Path) -> JurisdictionPack:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    code = str(raw["code"]).upper()
    kinds = [TaxIdKind(name) for name in raw["kinds"]]
    for kind in kinds:
        if kind not in KIND_SPECS:
            raise ValueError(f"{path.name}: {kind.value} has no spec")
    # `other` is always available, so any jurisdiction can hold a document
    # its pack never anticipated.
    if TaxIdKind.OTHER not in kinds:
        kinds.append(TaxIdKind.OTHER)
    return JurisdictionPack(code=code, kinds=tuple(kinds))


@lru_cache(maxsize=1)
def _packs() -> dict[str, JurisdictionPack]:
    """Parsed once per process. The files ship with the image."""
    return {pack.code: pack for pack in (_load_pack(p) for p in sorted(PACK_DIR.glob("*.toml")))}


def available_jurisdictions() -> list[str]:
    """Codes a pack exists for, for the settings selector."""
    return sorted(_packs())


def pack_for(jurisdiction: str | None) -> JurisdictionPack:
    """The pack for a workspace, or FALLBACK when unset or unshipped."""
    if not jurisdiction:
        return FALLBACK
    return _packs().get(jurisdiction.strip().upper(), FALLBACK)


def normalise_and_validate(kind: TaxIdKind, value: str) -> tuple[str, str | None]:
    """Store-ready value, plus a reason when it should be refused.

    Takes no jurisdiction: a document is checked by what it is, not by who
    is holding it. The jurisdiction decides only what a workspace is offered.
    """
    spec = spec_for(kind)
    normalised = spec.normalised(value)
    return normalised, spec.error_for(normalised)
