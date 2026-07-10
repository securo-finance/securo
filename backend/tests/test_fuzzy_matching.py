"""Tests for fuzzy transaction matching logic (Phase 2)."""

import pytest

from app.services.connection_service import _description_similarity


class TestDescriptionSimilarity:
    """Unit tests for the _description_similarity helper."""

    def test_identical_descriptions(self):
        assert _description_similarity("UBER TRIP", "UBER TRIP") == 1.0

    def test_partial_overlap(self):
        # "UBER" overlaps, "TRIP" vs "RIDE" don't → 1/2 = 0.5
        score = _description_similarity("UBER TRIP", "UBER RIDE")
        assert score == pytest.approx(0.5)

    def test_no_overlap(self):
        score = _description_similarity("NETFLIX", "SPOTIFY")
        assert score == 0.0

    def test_case_insensitive(self):
        score = _description_similarity("Uber Trip", "UBER TRIP")
        assert score == 1.0

    def test_null_first_arg(self):
        assert _description_similarity(None, "UBER") == 0.0

    def test_null_second_arg(self):
        assert _description_similarity("UBER", None) == 0.0

    def test_both_null(self):
        assert _description_similarity(None, None) == 0.0

    def test_empty_first_arg(self):
        assert _description_similarity("", "UBER") == 0.0

    def test_empty_second_arg(self):
        assert _description_similarity("UBER", "") == 0.0

    def test_single_token_match(self):
        # "IFOOD" matches in both → 1/2 = 0.5
        score = _description_similarity("IFOOD RESTAURANTE", "IFOOD")
        assert score == pytest.approx(0.5)

    def test_high_overlap_above_threshold(self):
        # 3 out of 4 tokens match → 0.75
        score = _description_similarity("PIX RECEBIDO JOAO", "PIX RECEBIDO JOAO SILVA")
        assert score >= 0.6

    def test_low_overlap_below_threshold(self):
        # Only 1 out of 3 tokens match → 0.33
        score = _description_similarity("PAGAMENTO PIX", "UBER TRIP PIX")
        assert score < 0.6
