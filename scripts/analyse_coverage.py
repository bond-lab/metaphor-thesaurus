#!/usr/bin/env python3
"""
Analyse how well source_domain_map.toml synsets cover the thesaurus entries,
and whether going up WordNet levels from the domain synset recovers more matches.

For each SOURCE domain, reports:
  lemma    — entries already matched via the existing lemma-based hyper_matched
  exact    — entries whose literal synset is a hyponym of the domain's canonical synset
  +1 / +2  — additional entries matched by going 1 or 2 WordNet levels up from
              the domain synset (i.e. using a more general domain synset)
  miss     — entries still unmatched at +2 levels

An entry "belongs to" a SOURCE domain if its containing theme name is of the
form "TARGET IS SOURCE" and SOURCE is in the domain map.

Usage:
  uv run scripts/analyse_coverage.py
  uv run scripts/analyse_coverage.py --levels 3 --top 30 --min-entries 5
"""

import argparse
import json
import re
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

import wn
from functools import lru_cache

sys.path.insert(0, str(Path(__file__).parent))
from wordnet_match import setup_wn


# ---------------------------------------------------------------------------
# Synset helpers
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def get_synset(sid: str) -> object | None:
    try:
        return wn.synset(sid)
    except wn.Error:
        return None


@lru_cache(maxsize=None)
def ancestor_ids(synset) -> frozenset[str]:
    """Set of synset IDs in the full hypernym closure (including self)."""
    ids = {s.id for path in synset.hypernym_paths() for s in path}
    ids.add(synset.id)
    return frozenset(ids)


def synsets_at_level(synsets: list, level: int) -> set[str]:
    """
    Starting from `synsets`, walk `level` steps up the hypernym graph.
    Returns the set of synset IDs reachable at exactly `level` steps
    (BFS, may fan out across multiple hypernym paths).
    """
    frontier = set(synsets)
    for _ in range(level):
        next_frontier = set()
        for ss in frontier:
            next_frontier.update(ss.hypernyms())
        if not next_frontier:
            break
        frontier = next_frontier
    return {ss.id for ss in frontier}


# ---------------------------------------------------------------------------
# Domain map parsing
# ---------------------------------------------------------------------------

def load_domain_synsets(toml_path: Path) -> dict[str, list[object]]:
    """
    Parse source_domain_map.toml → {domain_name: [synset, ...]}

    For single-leaf domains (name has no trailing -N): the domain synset.
    For grouping nodes: the synsets of all DOMAIN-N children.
    """
    with open(toml_path, "rb") as f:
        raw = tomllib.load(f)

    pos_tops = {"N_TOP", "V_TOP", "A_TOP", "R_TOP"}
    entries = raw.get("domains", [])

    # Find grouping nodes (no synset_id, not a POS root)
    grouping = {e["name"] for e in entries
                if not e["synset_id"] and e["name"] not in pos_tops}

    domain_synsets: dict[str, list[object]] = defaultdict(list)
    for e in entries:
        if not e["synset_id"]:
            continue
        ss = get_synset(e["synset_id"])
        if ss is None:
            continue
        name = e["name"]
        # Child of a grouping node (e.g. BIRD-3): attribute to parent
        if re.search(r"-\d+$", name) and e.get("hypernym") in grouping:
            domain_synsets[e["hypernym"]].append(ss)
        else:
            domain_synsets[name].append(ss)

    return dict(domain_synsets)


# ---------------------------------------------------------------------------
# Theme SOURCE extraction
# ---------------------------------------------------------------------------

def extract_sources(theme_name: str, known: set[str]) -> list[str]:
    """
    From "TARGET IS SOURCE1/SOURCE2" return the SOURCE parts that are known domains.
    """
    parts = theme_name.split(" IS ", 1)
    if len(parts) < 2:
        return []
    return [s.strip() for s in parts[1].split("/") if s.strip() in known]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Domain-map coverage analysis vs WordNet hypernym levels"
    )
    parser.add_argument("--input",       default="build/thesaurus_wn.json")
    parser.add_argument("--domainmap",   default="build/source_domain_map.toml")
    parser.add_argument("--levels",      type=int, default=2,
                        help="WN levels up from domain synset to check (default: 2)")
    parser.add_argument("--top",         type=int, default=0,
                        help="Show only top N domains by entry count (0=all)")
    parser.add_argument("--min-entries", type=int, default=3,
                        help="Skip domains with fewer than N entries (default: 3)")
    args = parser.parse_args()

    setup_wn()

    data        = json.loads(Path(args.input).read_text())
    dom_synsets = load_domain_synsets(Path(args.domainmap))
    known       = set(dom_synsets.keys())

    # Counters per domain: {domain: {"total", "no_wn", "lemma", "exact", 1, 2, ...}}
    stats: dict[str, dict] = defaultdict(
        lambda: {"total": 0, "no_wn": 0, "lemma": 0, "exact": 0,
                 **{f"+{i}": 0 for i in range(1, args.levels + 1)}}
    )

    for part in data["parts"]:
        for theme in part["themes"]:
            sources = extract_sources(theme["name"], known)
            if not sources:
                continue
            for sub in theme["subsections"]:
                for entry in sub["entries"]:
                    lit = entry.get("wn_literal")
                    for src in sources:
                        stats[src]["total"] += 1
                        if not lit:
                            stats[src]["no_wn"] += 1
                            continue

                        hyper_matched = lit["scores"].get("hyper_matched", {})

                        # Lemma-based (current pipeline)
                        if src in hyper_matched:
                            stats[src]["lemma"] += 1

                        # Synset-based matching
                        entry_ss = get_synset(lit["synset_id"])
                        if entry_ss is None:
                            continue

                        ancestors = ancestor_ids(entry_ss)
                        dom_ss_list = dom_synsets[src]

                        # Level 0: exact match (entry is hyponym of domain synset)
                        if any(ss.id in ancestors for ss in dom_ss_list):
                            stats[src]["exact"] += 1
                            continue  # already matched, don't double-count in +N

                        # Levels 1..N: widen domain synset upward
                        matched_level = None
                        for level in range(1, args.levels + 1):
                            generalised = synsets_at_level(dom_ss_list, level)
                            if generalised & ancestors:
                                matched_level = level
                                break

                        if matched_level is not None:
                            stats[src][f"+{matched_level}"] += 1

    # ── Print table ──────────────────────────────────────────────────────────
    # Sort by total entries desc
    rows = [(d, s) for d, s in stats.items()
            if s["total"] >= args.min_entries and d in known]
    rows.sort(key=lambda x: x[1]["total"], reverse=True)
    if args.top:
        rows = rows[:args.top]

    lvl_headers = "  ".join(f"+{i}lv" for i in range(1, args.levels + 1))
    print(f"\n{'SOURCE DOMAIN':<26}  {'N':>5}  {'no_wn':>5}  "
          f"{'lemma':>5}  {'exact':>5}  {lvl_headers}  {'miss':>5}")
    print(f"  {'-'*24}  {'-'*5}  {'-'*5}  "
          f"{'-'*5}  {'-'*5}  {'  '.join(['-'*4]*args.levels)}  {'-'*5}")

    def pct(n, total):
        return f"{100*n//total:>3}%" if total else "  - "

    totals = defaultdict(int)
    for domain, s in rows:
        has_wn = s["total"] - s["no_wn"]
        lvl_cols = "  ".join(pct(s[f"+{i}"], has_wn) for i in range(1, args.levels + 1))
        missed = has_wn - s["exact"] - sum(s[f"+{i}"] for i in range(1, args.levels + 1))
        print(f"  {domain:<24}  {s['total']:>5}  {pct(s['no_wn'], s['total'])}  "
              f"{pct(s['lemma'], has_wn)}  {pct(s['exact'], has_wn)}  "
              f"{lvl_cols}  {pct(missed, has_wn)}")
        for k, v in s.items():
            totals[k] += v

    # Totals row
    has_wn = totals["total"] - totals["no_wn"]
    lvl_cols = "  ".join(pct(totals[f"+{i}"], has_wn) for i in range(1, args.levels + 1))
    missed = has_wn - totals["exact"] - sum(totals[f"+{i}"] for i in range(1, args.levels + 1))
    print(f"\n  {'TOTAL':<24}  {totals['total']:>5}  {pct(totals['no_wn'], totals['total'])}  "
          f"{pct(totals['lemma'], has_wn)}  {pct(totals['exact'], has_wn)}  "
          f"{lvl_cols}  {pct(missed, has_wn)}")

    print(f"\nColumns: N=entries in domain theme  no_wn=no WN sense found  "
          f"lemma=current pipeline  exact=hyponym of domain synset  "
          f"+Nlv=additional match N WN levels up  miss=unmatched at all levels")


if __name__ == "__main__":
    main()
