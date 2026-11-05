"""Review attribution data packets, not authority or identity lookups."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class HumanReviewPrincipal:
    user_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, UUID):
            raise ValueError("Human review principal requires a UUID user_id")


@dataclass(frozen=True)
class RuleReviewPrincipal:
    rule_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.rule_id, UUID):
            raise ValueError("Rule review principal requires a UUID rule_id")


ReviewAttribution = HumanReviewPrincipal | RuleReviewPrincipal
_UNSET = object()


def normalize_review_attribution(
    attribution: ReviewAttribution | UUID | None | object = _UNSET,
    *,
    actor_user_id: UUID | None | object = _UNSET,
) -> ReviewAttribution | None:
    """Validate explicit packets; map legacy UUIDs only to human principals."""
    if attribution is not _UNSET and actor_user_id is not _UNSET:
        raise ValueError("Supply attribution or actor_user_id, not both")
    if actor_user_id is not _UNSET:
        if actor_user_id is None:
            return None
        if not isinstance(actor_user_id, UUID):
            raise ValueError("actor_user_id must be a UUID")
        return HumanReviewPrincipal(actor_user_id)
    if attribution is _UNSET or attribution is None:
        return None
    if isinstance(attribution, UUID):
        return HumanReviewPrincipal(attribution)
    if isinstance(attribution, HumanReviewPrincipal):
        return HumanReviewPrincipal(attribution.user_id)
    if isinstance(attribution, RuleReviewPrincipal):
        return RuleReviewPrincipal(attribution.rule_id)
    raise ValueError("Invalid review attribution principal")
