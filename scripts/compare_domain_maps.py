#!/usr/bin/env python3
"""
Compare source and target domain maps, reporting:
  - Domains that appear only in one map
  - Domains that appear in both, with matching or differing canonical synsets

A domain "matches" if its highest-evidence synset (ignoring grouping nodes
and POS roots) is the same in both maps for every POS.

Usage:
  uv run scripts/compare_domain_maps.py
  uv run scripts/compare_domain_maps.py --source build/source_domain_map.toml \
                                         --target build/target_domain_map.toml
"""

import argparse
import tomllib
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_map(path: Path) -> dict[str, dict[str, str]]:
    """
    Parse a domain map TOML → {domain_name: {pos: synset_id}}.

    Grouping nodes (synset_id == "") and POS roots (*_TOP) are excluded.
    For DOMAIN-N children (multi-synset splits), map them back to their
    parent domain name so we compare at the domain level.
    """
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    pos_tops = {"N_TOP", "V_TOP", "A_TOP", "R_TOP"}
    entries = raw.get("domains", [])

    # Identify grouping nodes (no synset, not a POS root)
    grouping = {e["name"] for e in entries
                if not e["synset_id"] and e["name"] not in pos_tops}

    result: dict[str, dict[str, str]] = {}
    for e in entries:
        if not e["synset_id"] or e["name"] in pos_tops:
            continue
        name = e["name"]
        # If this is a DOMAIN-N child of a grouping node, attribute to parent
        import re
        m = re.search(r"-\d+$", name)
        if m and e.get("hypernym") in grouping:
            name = e["hypernym"]
        result.setdefault(name, {})
        # Keep the first (highest-evidence) synset per pos
        if e["pos"] not in result[name]:
            result[name][e["pos"]] = e["synset_id"]

    return result


def load_definitions(path: Path) -> dict[str, str]:
    """synset_id → definition from TOML."""
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return {e["synset_id"]: e["definition"]
            for e in raw.get("domains", []) if e.get("synset_id")}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare source and target domain maps"
    )
    parser.add_argument("--source",   default="build/source_domain_map.toml")
    parser.add_argument("--target",   default="build/target_domain_map.toml")
    parser.add_argument("--combined", default="build/combined_domain_map.toml",
                        help="Optional combined map for three-way comparison")
    args = parser.parse_args()

    src_path = Path(args.source)
    tgt_path = Path(args.target)

    if not src_path.exists():
        print(f"ERROR: {src_path} not found. Run build_domain_map.py --role source first.")
        return
    if not tgt_path.exists():
        print(f"ERROR: {tgt_path} not found. Run build_domain_map.py --role target first.")
        return

    src_map = load_map(src_path)
    tgt_map = load_map(tgt_path)
    src_defs = load_definitions(src_path)
    tgt_defs = load_definitions(tgt_path)

    # Optional combined map
    comb_map: dict[str, dict[str, str]] = {}
    if Path(args.combined).exists():
        comb_map = load_map(Path(args.combined))
        comb_defs = load_definitions(Path(args.combined))
    else:
        comb_defs = {}

    src_only  = sorted(set(src_map) - set(tgt_map))
    tgt_only  = sorted(set(tgt_map) - set(src_map))
    in_both   = sorted(set(src_map) & set(tgt_map))

    # Split "in both" into matching vs differing
    matching  = []
    differing = []
    for name in in_both:
        s = src_map[name]
        t = tgt_map[name]
        all_pos = sorted(set(s) | set(t))
        if all(s.get(p) == t.get(p) for p in all_pos):
            matching.append(name)
        else:
            differing.append(name)

    print(f"\nSource map : {len(src_map)} domains  ({src_path})")
    print(f"Target map : {len(tgt_map)} domains  ({tgt_path})")
    if comb_map:
        print(f"Combined   : {len(comb_map)} domains  ({args.combined})")

    print(f"\nSource-only : {len(src_only)}")
    print(f"Target-only : {len(tgt_only)}")
    print(f"In both     : {len(in_both)}  "
          f"({len(matching)} matching, {len(differing)} differing)")

    # ── Source-only ───────────────────────────────────────────────────────────
    if src_only:
        print(f"\n{'='*70}")
        print("  SOURCE-ONLY domains (not found in target map)")
        print(f"  {'-'*68}")
        for name in src_only:
            for pos, sid in sorted(src_map[name].items()):
                defn = src_defs.get(sid, "")[:60]
                print(f"  {name:<28} [{pos}] {sid:<28}  {defn}")

    # ── Target-only ───────────────────────────────────────────────────────────
    if tgt_only:
        print(f"\n{'='*70}")
        print("  TARGET-ONLY domains (not found in source map)")
        print(f"  {'-'*68}")
        for name in tgt_only:
            for pos, sid in sorted(tgt_map[name].items()):
                defn = tgt_defs.get(sid, "")[:60]
                print(f"  {name:<28} [{pos}] {sid:<28}  {defn}")

    # ── Differing domains ─────────────────────────────────────────────────────
    if differing:
        print(f"\n{'='*70}")
        print("  DIFFERING domains (same name, different canonical synset)")
        print(f"  {'-'*68}")
        for name in differing:
            s = src_map[name]
            t = tgt_map[name]
            c = comb_map.get(name, {})
            all_pos = sorted(set(s) | set(t))
            first = True
            for pos in all_pos:
                s_sid = s.get(pos, "—")
                t_sid = t.get(pos, "—")
                c_sid = c.get(pos, "—") if comb_map else None
                marker = "≠" if s_sid != t_sid else "="
                dom_col = name if first else ""
                s_defn = src_defs.get(s_sid, "")[:35]
                t_defn = tgt_defs.get(t_sid, "")[:35]
                row = (f"  {dom_col:<28} [{pos}] {marker}  "
                       f"src: {s_sid:<28} {s_defn}\n"
                       f"  {'':28}      "
                       f"tgt: {t_sid:<28} {t_defn}")
                if c_sid and c_sid != "—":
                    c_defn = comb_defs.get(c_sid, "")[:35]
                    row += (f"\n  {'':28}      "
                            f"comb:{c_sid:<28} {c_defn}")
                print(row)
                first = False
            print()

    # ── Matching ─────────────────────────────────────────────────────────────
    if matching:
        print(f"\n{'='*70}")
        print(f"  MATCHING domains ({len(matching)} — same canonical synset in both maps)")
        print(f"  {'-'*68}")
        for name in matching:
            for pos, sid in sorted(src_map[name].items()):
                defn = src_defs.get(sid, "")[:60]
                print(f"  {name:<28} [{pos}] {sid:<28}  {defn}")

    print()


if __name__ == "__main__":
    main()
