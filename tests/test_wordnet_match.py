"""
Tests for scripts/wordnet_match.py

Run from the repo root:
  .venv/bin/python tests/test_wordnet_match.py
"""

import json
import sys
from pathlib import Path

# Allow imports from scripts/
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import wn
from wordnet_match import (
    extract_domains,
    get_synsets,
    hypernym_matches,
    match_entry,
    overlap_scorer,
)

THESAURUS = Path(__file__).parent.parent / "build" / "thesaurus.json"


def test_overlap_scorer():
    assert overlap_scorer("precious stone mineral", "precious stone mineral") == 1.0, \
        "identical texts should score 1.0"
    assert overlap_scorer("cat sat mat", "dog ran fast") == 0.0, \
        "disjoint texts should score 0.0"
    score = overlap_scorer("a precious transparent stone of blue corundum",
                           "transparent bright blue precious stone")
    assert 0.3 < score < 0.8, f"unexpected partial overlap score {score}"
    print("PASS: overlap_scorer")


def test_hypernym_matches_sapphire():
    # Stone sense should reach 'mineral'
    ss_stone = next(
        ss for ss in wn.synsets("sapphire", pos="n")
        if "corundum" in (ss.definition() or "")
    )
    result = hypernym_matches(ss_stone, ["MINERAL"])
    assert result["score"] == 1.0, \
        f"expected MINERAL match for sapphire stone sense, got {result}"
    assert "MINERAL" in result["matched"], "MINERAL key missing from matched"
    print("PASS: hypernym_matches — sapphire stone → mineral")

    # Colour sense should reach 'colour' but NOT 'mineral'
    ss_colour = next(
        ss for ss in wn.synsets("sapphire", pos="n")
        if "shade of blue" in (ss.definition() or "")
    )
    r_colour  = hypernym_matches(ss_colour, ["COLOUR"])
    r_mineral = hypernym_matches(ss_colour, ["MINERAL"])
    assert r_colour["score"]  == 1.0, \
        f"expected COLOUR match for sapphire colour sense, got {r_colour}"
    assert r_mineral["score"] == 0.0, \
        f"MINERAL should not match sapphire colour sense, got {r_mineral}"
    print("PASS: hypernym_matches — sapphire colour → colour yes, mineral no")


def test_extract_domains():
    targets, sources = extract_domains("COLOUR IS MINERAL")
    assert targets == ["COLOUR"] and sources == ["MINERAL"], \
        f"single domain: got targets={targets} sources={sources}"

    targets2, sources2 = extract_domains("BAD/UNIMPORTANT IS POOR/CHEAP")
    assert targets2 == ["BAD", "UNIMPORTANT"] and sources2 == ["POOR", "CHEAP"], \
        f"multi domain: got targets={targets2} sources={sources2}"

    targets3, sources3 = extract_domains("NO IS VERB")
    assert targets3 == ["NO"] and sources3 == ["VERB"]

    print("PASS: extract_domains")


def test_end_to_end_sapphire():
    data = json.loads(THESAURUS.read_text())
    entry = next(
        e
        for part in data["parts"]
        for theme in part["themes"]
        for sub in theme["subsections"]
        for e in sub["entries"]
        if e["headword"].strip().lower() == "sapphire"
    )
    synsets = get_synsets("sapphire")
    result = match_entry(entry, "COLOUR IS MINERAL", synsets, 0.5,
                         overlap_scorer, "overlap")

    assert result["n_senses"] == 4, \
        f"expected 4 senses, got {result['n_senses']}"
    assert result["literal"]["synset_id"] == "omw-en-15019483-n", \
        f"literal: wrong synset {result['literal']['synset_id']}"
    assert result["metaphorical"]["synset_id"] == "omw-en-04969242-n", \
        f"metaphorical: wrong synset {result['metaphorical']['synset_id']}"
    print("PASS: end-to-end sapphire (overlap)")


if __name__ == "__main__":
    test_overlap_scorer()
    test_hypernym_matches_sapphire()
    test_extract_domains()
    test_end_to_end_sapphire()
    print("\nAll tests passed.")
