#!/usr/bin/env python3
"""
Analyse domain-to-synset consistency in thesaurus_wn.json.

For each SOURCE and TARGET domain word, show which WordNet synsets it
matched to via the hypernym chain, and how many times each synset was hit.
This reveals whether a domain word consistently maps to the same synset
(good) or scatters across many (noisy).

For domains with multiple matched synsets, computes their Lowest Common
Subsumer (LCS) — the deepest ancestor shared by all of them — as a
candidate canonical synset.

Usage:
  uv run analyse_domains.py
  uv run analyse_domains.py --min-count 2 --top 20
"""

import argparse
import json
import sys
from collections import defaultdict
from functools import lru_cache, reduce
from pathlib import Path

import wn
import wn.taxonomy

# Re-use setup_wn from wordnet_match
sys.path.insert(0, str(Path(__file__).parent))
from wordnet_match import setup_wn


@lru_cache(maxsize=None)
def _get_synset(sid: str) -> object | None:
    try:
        return wn.synset(sid)
    except wn.Error:
        return None


def _lcs_for_synsets(synsets: list) -> object | None:
    """Find the deepest ancestor shared by all synsets (must be same POS)."""
    if not synsets:
        return None
    if len(synsets) == 1:
        return synsets[0]
    def pair_lcs(a, b):
        if a is None or b is None:
            return None
        result = wn.taxonomy.lowest_common_hypernyms(a, b)
        return result[0] if result else None
    return reduce(pair_lcs, synsets)


def lcs_by_pos(synset_map: dict, min_count: int = 1) -> dict:
    """
    Group matched synsets by POS, compute LCS within each group.
    LCS is computed only over synsets with count >= min_count to avoid
    single-hit noise (e.g. 'early bird', 'jailbird') dragging the LCS
    to the root.

    Returns a dict keyed by POS with:
      { 'synsets': [...], 'total_count': int, 'lcs': synset | None }
    Sorted by total_count descending so the dominant POS is first.
    """
    by_pos: dict[str, dict] = defaultdict(lambda: {"synsets": [], "total_count": 0})

    for sid, rec in synset_map.items():
        ss = _get_synset(sid)
        if ss is None:
            continue
        pos = "a" if ss.pos == "s" else ss.pos
        by_pos[pos]["synsets"].append((ss, rec["count"]))
        by_pos[pos]["total_count"] += rec["count"]

    result = {}
    for pos, group in by_pos.items():
        # Only use synsets meeting min_count for LCS to filter noise
        lcs_candidates = [ss for ss, cnt in group["synsets"] if cnt >= min_count]
        result[pos] = {
            "synsets":     group["synsets"],        # [(ss, count), ...]
            "total_count": group["total_count"],
            "lcs":         _lcs_for_synsets(lcs_candidates),
        }

    return dict(sorted(result.items(), key=lambda kv: kv[1]["total_count"], reverse=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input",     default="build/thesaurus_wn.json")
    parser.add_argument("--min-count", type=int, default=1,
                        help="Only show synsets matched at least N times")
    parser.add_argument("--top",       type=int, default=0,
                        help="Show only top N domains by total match count (0=all)")
    args = parser.parse_args()

    setup_wn()
    data = json.loads(Path(args.input).read_text())

    # domain_word → role ("source"/"target") → synset_id → {count, definition, lemmas}
    source_hits: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"count": 0, "definition": "", "lemmas": []}))
    target_hits: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"count": 0, "definition": "", "lemmas": []}))

    for part in data["parts"]:
        for theme in part["themes"]:
            for sub in theme["subsections"]:
                for entry in sub["entries"]:
                    lit  = entry.get("wn_literal")
                    meta = entry.get("wn_metaphorical")

                    if lit:
                        for domain, hit in lit["scores"].get("hyper_matched", {}).items():
                            rec = source_hits[domain][hit["synset_id"]]
                            rec["count"] += 1
                            rec["definition"] = hit["definition"]
                            rec["lemmas"]     = hit["lemmas"]

                    if meta:
                        for domain, hit in meta["scores"].get("hyper_matched", {}).items():
                            rec = target_hits[domain][hit["synset_id"]]
                            rec["count"] += 1
                            rec["definition"] = hit["definition"]
                            rec["lemmas"]     = hit["lemmas"]

    def print_table(hits: dict, role: str) -> None:
        # Sort domains by total match count descending
        domains = sorted(hits.items(),
                         key=lambda kv: sum(v["count"] for v in kv[1].values()),
                         reverse=True)
        if args.top:
            domains = domains[: args.top]

        print(f"\n{'='*80}")
        print(f"  {role.upper()} DOMAINS")
        print(f"{'='*80}")
        print(f"  {'DOMAIN':<28}  {'SYNSET':<26}  {'CNT':>5}  DEFINITION (abridged)")
        print(f"  {'-'*28}  {'-'*26}  {'-'*5}  {'-'*30}")

        for domain, synset_map in domains:
            # Sort synsets by count descending
            synsets = sorted(synset_map.items(),
                             key=lambda kv: kv[1]["count"], reverse=True)
            synsets = [(sid, rec) for sid, rec in synsets
                       if rec["count"] >= args.min_count]
            if not synsets:
                continue

            total = sum(rec["count"] for _, rec in synsets)
            n_synsets = len(synsets)

            pos_groups = lcs_by_pos(synset_map, min_count=args.min_count)

            first_domain = True
            for pos, group in pos_groups.items():
                grp_total = group["total_count"]
                grp_pct   = 100 * grp_total / total if total else 0
                grp_synsets = sorted(group["synsets"], key=lambda x: x[1], reverse=True)
                grp_synsets = [(ss, cnt) for ss, cnt in grp_synsets
                               if cnt >= args.min_count]
                if not grp_synsets:
                    continue

                for i, (ss, cnt) in enumerate(grp_synsets):
                    dom_col   = domain if first_domain and i == 0 else ""
                    tot_col   = f"({total})" if first_domain and i == 0 else ""
                    pos_col   = f"[{pos}]" if i == 0 else ""
                    defn = (ss.definition() or "")
                    defn_short = defn[:45] + "…" if len(defn) > 45 else defn
                    pct = 100 * cnt / total if total else 0
                    print(f"  {dom_col:<20} {tot_col:>7}  {pos_col:<4}"
                          f"  {ss.id:<26}  {cnt:>5}  {pct:>4.0f}%  {defn_short}")
                    first_domain = False

                # LCS for this POS group (only if >1 synset in group)
                if len(grp_synsets) > 1 and group["lcs"]:
                    lcs = group["lcs"]
                    defn = (lcs.definition() or "")
                    defn_short = defn[:55] + "…" if len(defn) > 55 else defn
                    lcs_count = sum(cnt for _, cnt in grp_synsets)
                    lcs_pct   = 100 * lcs_count / total if total else 0
                    print(f"  {'':20}  {'':>7}  {'':4}"
                          f"  {'LCS['+pos+']':<26}  {lcs_count:>5}  "
                          f"{lcs_pct:>4.0f}%  {defn_short}")

            if n_synsets > 1:
                print()

    print_table(source_hits, "source")
    print_table(target_hits, "target")


if __name__ == "__main__":
    main()
