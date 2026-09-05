"""What the active workspace's jurisdiction asks for.

The frontend needs labels, masks and ordering to render a document field; the
backend needs normalisation and validation to accept one. Both come from the
same definition, served here, so the two cannot drift into disagreeing about
what a CNPJ looks like.

The response is presentation metadata only. It carries no workspace data and
changes only when the deployment's packs change, so it caches like
`/api/info`.
"""
from fastapi import APIRouter, Depends

from app.core.workspace_context import WorkspaceContext, current_workspace
from app.fiscal.registry import (
    TaxIdKind,
    available_jurisdictions,
    pack_for,
    spec_for,
)

router = APIRouter(prefix="/api/fiscal", tags=["fiscal"])


def _kind_payload(kind: TaxIdKind, offered: bool) -> dict:
    spec = spec_for(kind)
    return {
        "kind": kind.value,
        "label_key": spec.label_key,
        "mask": spec.mask,
        # True when this jurisdiction asks for it. Everything else stays
        # selectable: the counterparty's country is not the workspace's, so a
        # BR workspace still has to be able to store a German VAT number.
        "offered": offered,
    }


@router.get("/jurisdictions")
async def list_jurisdictions():
    """Codes a pack ships for, for the workspace settings selector.

    An empty jurisdiction is a valid choice, not a missing one, so the
    frontend offers it alongside these.
    """
    return {"jurisdictions": available_jurisdictions()}


@router.get("/tax-id-kinds")
async def list_tax_id_kinds(ctx: WorkspaceContext = Depends(current_workspace)):
    """Document kinds for the active workspace, plus which country uses what.

    Two lists, because the picker needs both:

      - `kinds`: every document, with the ones this jurisdiction asks for
        first and flagged `offered`. The rest stay selectable, since a
        counterparty's country is not the workspace's.
      - `jurisdictions`: the country-to-documents map, so the picker can
        group by country and be searched by country name. It has to come
        from here: which documents a country uses *is* the pack, and a
        second copy in the frontend would drift from it.
    """
    pack = pack_for(ctx.workspace.tax_jurisdiction)
    offered = list(pack.kinds)
    rest = [kind for kind in TaxIdKind if kind not in offered]
    return {
        "jurisdiction": pack.code or None,
        "kinds": [_kind_payload(kind, True) for kind in offered]
        + [_kind_payload(kind, False) for kind in rest],
        "jurisdictions": [
            {
                "code": code,
                # `other` is appended to every pack and would be noise
                # repeated under each country; the picker offers it once.
                "kinds": [
                    kind.value
                    for kind in pack_for(code).kinds
                    if kind is not TaxIdKind.OTHER
                ],
            }
            for code in available_jurisdictions()
        ],
    }
