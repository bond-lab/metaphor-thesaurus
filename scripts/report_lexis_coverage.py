#!/usr/bin/env python3
"""Compare the guide's lexis index against the extracted thesaurus headwords.

Classifies every guide entry that is absent from the extraction into one of
six categories and writes:

  paper/lexis_coverage_report.txt  — human-readable summary + full lists
  paper/lexis_coverage.tsv         — tab-separated: category TAB guide_form TAB our_form

Usage:
  uv run scripts/report_lexis_coverage.py
  uv run scripts/report_lexis_coverage.py --thesaurus build/thesaurus.json
"""

import argparse
import re
from collections import Counter, defaultdict
from pathlib import Path

import docx

_REPO = Path(__file__).parent.parent

CATEGORIES = [
    "guide_strips_article",
    "guide_short_form",
    "slash_combined",
    "different_phrasing",
    "hyphen_variant",
    "guide_typo",
    "absent",
]

CAT_LABELS = {
    "guide_strips_article": "Guide strips leading article/preposition",
    "guide_short_form":     "Guide uses shorter base form",
    "slash_combined":       "Guide picks one slash-alternative",
    "different_phrasing":   "Similar but different phrasing in thesaurus",
    "hyphen_variant":       "Hyphenation / spacing variant",
    "guide_typo":           "Guide typo or garbled entry",
    "absent":               "Not found under any matching form",
}

CAT_NOTES = {
    "guide_strips_article": (
        "The guide's alphabetical index omits a leading article or preposition "
        "(a, an, the, at, in, to) that the thesaurus retains.  "
        "E.g. guide 'about face' → thesaurus 'an about face'."
    ),
    "guide_short_form": (
        "The guide indexes a shorter or base form of a headword; the thesaurus "
        "stores the full expression.  "
        "E.g. guide 'adhere' → thesaurus 'adhere to'; "
        "guide 'against the stream' → thesaurus 'against the stream/tide'."
    ),
    "slash_combined": (
        "The guide indexes one slash-alternative in isolation; the thesaurus "
        "stores the combined multi-alternative headword.  "
        "E.g. guide 'any road' → thesaurus 'anyway/any road'."
    ),
    "different_phrasing": (
        "The entry exists in the thesaurus but under a substantively different "
        "phrasing — word order, added parenthetical, or expanded repetition.  "
        "E.g. guide 'blow by blow account/description' → thesaurus "
        "'blow by blow account/ blow by blow description'."
    ),
    "hyphen_variant": (
        "The entry exists but with a different hyphenation convention.  "
        "E.g. guide 'arm-candy' → thesaurus 'arm candy'."
    ),
    "guide_typo": (
        "The guide entry appears garbled or is a known typographic error.  "
        "E.g. 'amily jewels' for 'family jewels'."
    ),
    "absent": (
        "The entry could not be matched to any form in the extracted thesaurus.  "
        "This includes a small number of entries that do not appear in the "
        "thesaurus docx at all (possible guide errors or later removals)."
    ),
}


def _build_guide_lexis(guide_path: Path) -> dict[str, str]:
    """Return {normalised_headword: raw_line} for guide section 7 entries with page numbers."""
    doc = docx.Document(str(guide_path))
    paras = [(i, p.text.strip()) for i, p in enumerate(doc.paragraphs) if p.text.strip()]
    hits  = [i for i, t in paras if "INDEX OF LEXIS" in t]
    if len(hits) < 2:
        raise SystemExit("Could not locate section 7 body in guide")
    s7_start = hits[1]

    result = {}
    for i, t in paras:
        if i <= s7_start:
            continue
        if not re.match(r"^[a-z'\(]", t):
            continue
        if not re.search(r",\s*\d", t):
            continue   # category header without page number
        hw = re.sub(r",\s*\d[\d,\s]*$", "", t).strip().lower()
        if hw:
            result[hw] = t
    return result


def _build_our_headwords(thesaurus_path: Path) -> set[str]:
    import json
    data = json.loads(thesaurus_path.read_text())
    return {
        e["headword"].strip().lower()
        for p in data["parts"]
        for t in p["themes"]
        for s in t["subsections"]
        for e in s["entries"]
    }


def _classify(hw: str, raw: str, our_hws: set[str]) -> tuple[str, str]:
    """Return (category, matched_form_or_note)."""
    # 1. Guide typo / garbled
    if re.match(r"^[a-z]{1,2}[A-Z']", hw) or hw in ("amily jewels",):
        return "guide_typo", ""

    # 2. Hyphenation variant
    for variant in [hw.replace("-", " "), hw.replace("-", ""), hw.replace(" ", "-")]:
        if variant != hw and variant in our_hws:
            return "hyphen_variant", variant

    # 3. Guide stripped a leading article/preposition
    for particle in ("a ", "an ", "the ", "at ", "to ", "in "):
        if (particle + hw) in our_hws:
            return "guide_strips_article", particle + hw

    # 4. Guide uses shorter/base form; we have the full expression
    longer = sorted(o for o in our_hws if o.startswith(hw + " ") or o.startswith(hw + "/"))
    if longer:
        return "guide_short_form", longer[0]

    # 5. Guide picks one slash-alternative; we have the combined form
    slash_matches = sorted(
        o for o in our_hws
        if hw in o.split("/") or any(hw == p.strip() for p in o.split("/"))
    )
    if slash_matches:
        return "slash_combined", slash_matches[0]

    # 6. Same first word, different phrasing
    core = re.sub(r"\([^)]*\)", "", hw).strip().split()[0]
    similar = sorted(o for o in our_hws if o.startswith(core))
    if similar:
        return "different_phrasing", similar[0]

    return "absent", ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--guide",     default="external/GUIDE_TO_USING_THE_THESAURUS.docx")
    parser.add_argument("--thesaurus", default="build/thesaurus.json")
    parser.add_argument("--out-txt",   default="paper/lexis_coverage_report.txt")
    parser.add_argument("--out-tsv",   default="paper/lexis_coverage.tsv")
    args = parser.parse_args()

    guide_path = _REPO / args.guide
    thes_path  = _REPO / args.thesaurus

    if not guide_path.exists():
        raise SystemExit(f"Guide not found: {guide_path}")
    if not thes_path.exists():
        raise SystemExit(f"Thesaurus not found: {thes_path} — run extract.py first")

    print("Loading guide lexis index …")
    guide_lexis = _build_guide_lexis(guide_path)
    print("Loading thesaurus headwords …")
    our_hws = _build_our_headwords(thes_path)

    present = {hw for hw in guide_lexis if hw in our_hws}
    missing = {hw: guide_lexis[hw] for hw in guide_lexis if hw not in our_hws}

    # Classify each missing entry
    rows: dict[str, list[tuple[str, str, str]]] = defaultdict(list)  # cat → [(guide, raw, matched)]
    counts = Counter()
    for hw in sorted(missing):
        cat, matched = _classify(hw, missing[hw], our_hws)
        rows[cat].append((hw, missing[hw], matched))
        counts[cat] += 1

    total       = len(guide_lexis)
    n_present   = len(present)
    n_missing   = len(missing)
    coverage    = n_present / total

    # ── Write plain-text report ───────────────────────────────────────────
    out_txt = _REPO / args.out_txt
    out_txt.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    w = lines.append

    w("LEXIS INDEX COVERAGE REPORT")
    w("=" * 70)
    w(f"Guide entries with page numbers : {total:6d}")
    w(f"Present in extracted thesaurus  : {n_present:6d}  ({coverage:.1%})")
    w(f"Missing                         : {n_missing:6d}  ({1-coverage:.1%})")
    w("")
    w("SUMMARY BY CATEGORY")
    w("-" * 70)
    w(f"  {'Category':<45}  {'Count':>5}  {'%':>5}")
    w(f"  {'-'*45}  {'-----':>5}  {'-----':>5}")
    for cat in CATEGORIES:
        n = counts[cat]
        pct = n / total * 100
        w(f"  {CAT_LABELS[cat]:<45}  {n:>5}  {pct:>4.1f}%")
    w("")

    for cat in CATEGORIES:
        if not rows[cat]:
            continue
        w("")
        w("=" * 70)
        w(f"CATEGORY: {CAT_LABELS[cat]}")
        w(f"Count: {counts[cat]}")
        w("")
        w(CAT_NOTES[cat])
        w("")
        w(f"  {'Guide form':<45}  {'Thesaurus form / note'}")
        w(f"  {'-'*45}  {'-'*30}")
        for hw, raw, matched in rows[cat]:
            w(f"  {hw:<45}  {matched}")
        w("")

    out_txt.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report → {out_txt}")

    # ── Write TSV ─────────────────────────────────────────────────────────
    out_tsv = _REPO / args.out_tsv
    tsv_lines = ["category\tguide_form\traw_guide_entry\tthesaurus_form"]
    for cat in CATEGORIES:
        for hw, raw, matched in rows[cat]:
            tsv_lines.append(f"{cat}\t{hw}\t{raw}\t{matched}")
    out_tsv.write_text("\n".join(tsv_lines), encoding="utf-8")
    print(f"TSV    → {out_tsv}")

    # ── Print summary to stdout ───────────────────────────────────────────
    print()
    print(f"Guide: {total}  Present: {n_present} ({coverage:.1%})  Missing: {n_missing}")
    for cat in CATEGORIES:
        if counts[cat]:
            print(f"  {counts[cat]:4d}  {CAT_LABELS[cat]}")


if __name__ == "__main__":
    main()
