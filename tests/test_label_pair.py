"""Tests for scripts/label_pair.py"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from label_pair import LabelResult, MetaphorLabeler, _extract_domains

_BUILD = Path(__file__).parent.parent / "build"
_THESAURUS_WN = _BUILD / "thesaurus_wn.json"

try:
    from label_pair import setup_wn
    setup_wn()
    _WN_AVAILABLE = True
except Exception:
    _WN_AVAILABLE = False

requires_wn = pytest.mark.skipif(
    not _WN_AVAILABLE, reason="WordNet data unavailable"
)
requires_thesaurus_wn = pytest.mark.skipif(
    not _THESAURUS_WN.exists(),
    reason="build/thesaurus_wn.json not found — run build.sh first",
)


# ---------------------------------------------------------------------------
# Unit tests — no WN or build artefacts needed
# ---------------------------------------------------------------------------

def test_normalise_id_short():
    assert MetaphorLabeler._normalise_id("15019483-n") == "omw-en-15019483-n"


def test_normalise_id_full():
    assert MetaphorLabeler._normalise_id("omw-en-15019483-n") == "omw-en-15019483-n"


def test_normalise_id_no_dash():
    assert MetaphorLabeler._normalise_id("04969242n") == "omw-en-04969242-n"


def test_extract_domains_simple():
    targets, sources = _extract_domains("COLOUR IS MINERAL")
    assert targets == ["COLOUR"]
    assert sources == ["MINERAL"]


def test_extract_domains_multi():
    targets, sources = _extract_domains("BAD/UNIMPORTANT IS POOR/CHEAP")
    assert targets == ["BAD", "UNIMPORTANT"]
    assert sources == ["POOR", "CHEAP"]


def test_label_result_fields():
    r = LabelResult(
        label="COLOUR IS MINERAL",
        tier=1,
        confidence=0.8,
        source_domain="MINERAL",
        target_domain="COLOUR",
        evidence={},
    )
    assert r.label == "COLOUR IS MINERAL"
    assert r.tier == 1
    assert 0.0 <= r.confidence <= 1.0


# ---------------------------------------------------------------------------
# Tier 1 — exact pair from thesaurus_wn.json
# ---------------------------------------------------------------------------

@requires_wn
@requires_thesaurus_wn
def test_tier1_sapphire():
    """The sapphire pair is the running example in the paper; must be Tier 1."""
    labeler = MetaphorLabeler.from_build(_BUILD)
    result  = labeler.label("15019483-n", "04969242-n")

    assert result.tier == 1, f"expected Tier 1, got Tier {result.tier}"
    assert result.label == "COLOUR IS MINERAL", f"unexpected label: {result.label}"
    assert result.source_domain == "MINERAL"
    assert result.target_domain == "COLOUR"
    assert 0.0 < result.confidence <= 1.0
    assert result.evidence.get("headword") == "sapphire"


@requires_wn
@requires_thesaurus_wn
def test_tier1_confidence_in_range():
    """Tier-1 confidence must always be in [0, 1]."""
    labeler = MetaphorLabeler.from_build(_BUILD)
    result  = labeler.label("15019483-n", "04969242-n")
    assert 0.0 <= result.confidence <= 1.0


# ---------------------------------------------------------------------------
# Tier 2 — domain map matching
# ---------------------------------------------------------------------------

@requires_wn
@requires_thesaurus_wn
def test_tier2_reversed_pair():
    """Reversing the sapphire pair should still find a valid label (different tier)."""
    labeler = MetaphorLabeler.from_build(_BUILD)
    # Swap source and target — no longer an attested pair, should fall to Tier 2 or 3
    result = labeler.label("04969242-n", "15019483-n")
    assert result.tier in (2, 3), f"expected Tier 2 or 3, got {result.tier}"
    assert "IS" in result.label
    assert 0.0 <= result.confidence <= 1.0


@requires_wn
@requires_thesaurus_wn
def test_tier2_returns_label_result():
    """Any synset pair should return a LabelResult (no exceptions)."""
    labeler = MetaphorLabeler.from_build(_BUILD)
    # Use two valid synsets from omw-en that are unlikely to be an exact pair
    # 02084071-n = dog;  04959672-n = chromatic colour
    result = labeler.label("02084071-n", "04959672-n")
    assert isinstance(result, LabelResult)
    assert result.label
    assert "IS" in result.label


# ---------------------------------------------------------------------------
# Tier 3 — partial / fallback
# ---------------------------------------------------------------------------

@requires_wn
@requires_thesaurus_wn
def test_tier3_confidence_capped():
    """Tier-3 confidence must be ≤ 0.5."""
    labeler = MetaphorLabeler.from_build(_BUILD)
    # Find a pair that resolves to Tier 3 by using an unattested combination
    # 09204584-n = geological formation (unlikely target); 02084071-n = dog
    result = labeler.label("09204584-n", "02084071-n")
    if result.tier == 3:
        assert result.confidence <= 0.5, (
            f"Tier-3 confidence {result.confidence} exceeds 0.5"
        )


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

@requires_wn
def test_graceful_without_thesaurus_wn(tmp_path):
    """Labeler works without thesaurus_wn.json (degrades to Tier 2/3)."""
    labeler = MetaphorLabeler(
        thesaurus_wn_path = None,
        source_map_path   = _BUILD / "source_domain_map.toml",
        target_map_path   = _BUILD / "target_domain_map.toml",
        thesaurus_path    = _BUILD / "thesaurus.json",
    )
    result = labeler.label("15019483-n", "04969242-n")
    assert isinstance(result, LabelResult)
    # Without thesaurus_wn.json there can be no Tier 1
    assert result.tier in (2, 3)
    assert "IS" in result.label


@requires_wn
def test_unknown_synset_id():
    """An unrecognised synset ID should not raise; falls back gracefully."""
    labeler = MetaphorLabeler(
        thesaurus_wn_path = None,
        source_map_path   = None,
        target_map_path   = None,
        thesaurus_path    = _BUILD / "thesaurus.json",
    )
    result = labeler.label("00000000-n", "00000001-n")
    assert isinstance(result, LabelResult)
    assert result.tier == 3
    assert result.confidence == 0.0 or result.confidence <= 0.5
