"""Tests for fuzzy transaction matching logic (Phase 2)."""
import pytest

from app.services.text_similarity import token_overlap


class TestDescriptionSimilarity:
    """Unit tests for the shared token-overlap helper."""

    def test_identical_descriptions(self):
        assert token_overlap("UBER TRIP", "UBER TRIP") == 1.0

    def test_partial_overlap(self):
        # "UBER" overlaps, "TRIP" vs "RIDE" don't → 1/2 = 0.5
        score = token_overlap("UBER TRIP", "UBER RIDE")
        assert score == pytest.approx(0.5)

    def test_no_overlap(self):
        score = token_overlap("NETFLIX", "SPOTIFY")
        assert score == 0.0

    def test_case_insensitive(self):
        score = token_overlap("Uber Trip", "UBER TRIP")
        assert score == 1.0

    def test_null_first_arg(self):
        assert token_overlap(None, "UBER") == 0.0

    def test_null_second_arg(self):
        assert token_overlap("UBER", None) == 0.0

    def test_both_null(self):
        assert token_overlap(None, None) == 0.0

    def test_empty_first_arg(self):
        assert token_overlap("", "UBER") == 0.0

    def test_empty_second_arg(self):
        assert token_overlap("UBER", "") == 0.0

    def test_single_token_match(self):
        # "IFOOD" matches in both → 1/2 = 0.5
        score = token_overlap("IFOOD RESTAURANTE", "IFOOD")
        assert score == pytest.approx(0.5)

    def test_high_overlap_above_threshold(self):
        # 3 out of 4 tokens match → 0.75
        score = token_overlap("PIX RECEBIDO JOAO", "PIX RECEBIDO JOAO SILVA")
        assert score >= 0.6

    def test_low_overlap_below_threshold(self):
        # Only 1 out of 3 tokens match → 0.33
        score = token_overlap("PAGAMENTO PIX", "UBER TRIP PIX")
        assert score < 0.6
