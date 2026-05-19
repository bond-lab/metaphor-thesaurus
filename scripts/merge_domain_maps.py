#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["wn>=1.1"]
# ///
"""Merge source and target domain maps into a single domain_map.toml for review.

Reads build/source_domain_map.toml and build/target_domain_map.toml, merges
their entries (deduplicating by base_name × pos × synset_id), enriches each
matched entry with its ILI and primary WN lemma, drops grouping/root nodes,
removes within-group hyponyms, and adds stub entries for thesaurus domains
absent from both maps.  Result is sorted by POS (n → v → a → r → unknown).

Every entry gets use = false.  The reviewer sets use = true for entries to
keep and fills in pos/synset_id for stubs.  For domains with multiple
candidate synsets the reviewer picks the best one.

Usage:
    python scripts/merge_domain_maps.py [options]
    uv run scripts/merge_domain_maps.py

Options:
    --source     path to source_domain_map.toml  (default: build/source_domain_map.toml)
    --target     path to target_domain_map.toml  (default: build/target_domain_map.toml)
    --thesaurus  path to thesaurus.json          (default: build/thesaurus.json)
    --out        output path                     (default: domain_map.toml)
"""

import argparse
import json
import re
import tomllib
from collections import defaultdict, deque
from pathlib import Path

import wn

_BUILD_WN_DATA = Path(__file__).parent.parent / "build" / "wn-data"
_OMW_EN_SPECIFIER = "omw-en:2.0"
_POS_ORDER = {"n": 0, "v": 1, "a": 2, "r": 3}


def _setup_wn() -> None:
    """Point wn at the local build/wn-data directory."""
    _BUILD_WN_DATA.mkdir(parents=True, exist_ok=True)
    wn.config.data_directory = _BUILD_WN_DATA
    existing = {lx.specifier() for lx in wn.lexicons()}
    if _OMW_EN_SPECIFIER not in existing:
        wn.download(_OMW_EN_SPECIFIER)


def _toml_str(s: str) -> str:
    """Escape a string for use inside TOML double-quoted values."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _load_entries(path: Path) -> list[dict]:
    """Load a domain map TOML and return the list of domain entries."""
    with path.open("rb") as f:
        data = tomllib.load(f)
    return data.get("domains", [])


def _merge_maps(src: list[dict], tgt: list[dict]) -> dict[tuple, dict]:
    """Merge source and target entries, deduplicating by (base_name, pos, synset_id).

    The split suffix is stripped before keying, so PLANT-1 and PLANT-2 that
    resolve to the same synset collapse into a single PLANT entry.  Entries
    with the same base name but different synset_ids (e.g. FOOD resolving
    differently in source vs target roles) are kept as separate entries, both
    named FOOD.

    Args:
        src: entries from source_domain_map.toml.
        tgt: entries from target_domain_map.toml.

    Returns:
        Dict keyed by (base_name, pos, synset_id) with name normalised to
        base name.
    """
    merged: dict[tuple, dict] = {}
    for entry in src + tgt:
        base = re.sub(r"-\d+$", "", entry["name"])
        key = (base, entry["pos"], entry["synset_id"])
        if key not in merged:
            e = dict(entry)
            e["name"] = base
            merged[key] = e
    return merged


def _remove_parent_nodes(merged: dict[tuple, dict]) -> dict[tuple, dict]:
    """Drop entries with no synset_id that act as parents of other entries.

    Removes POS roots (N_TOP, …) and grouping nodes.  Stub entries (thesaurus
    domains with no WN match and no children) are kept for the reviewer.

    Args:
        merged: merged entry dict (not mutated).

    Returns:
        Filtered dict with parent-only nodes removed.
    """
    parent_names: set[str] = {e["hypernym"] for e in merged.values() if e["hypernym"]}
    return {
        k: v
        for k, v in merged.items()
        if v["synset_id"] or v["name"] not in parent_names
    }


def _clear_self_hypernyms(entries: list[dict]) -> None:
    """Clear hypernym when it equals the entry's own name.

    After base-name normalisation (SEABIRD-N → SEABIRD) the hypernym field
    that pointed to the now-removed grouping node becomes a self-reference.
    This mutates entries in place.

    Args:
        entries: list of entry dicts (mutated in place).
    """
    for e in entries:
        if e.get("hypernym") == e.get("name"):
            e["hypernym"] = ""


def _add_missing_stubs(merged: dict[tuple, dict], all_domain_names: set[str]) -> None:
    """Add stub entries for thesaurus domains absent from the merged map.

    A domain is considered present if its base name appears among the merged
    keys.  Missing domains get empty pos, synset_id, hypernym, and definition.

    Args:
        merged:           existing merged entries (mutated in place).
        all_domain_names: union of sources and targets from thesaurus.json.
    """
    present_bases = {re.sub(r"-\d+$", "", k[0]) for k in merged}
    for name in sorted(all_domain_names - present_bases):
        key = (name, "", "")
        merged[key] = {
            "name": name,
            "pos": "",
            "synset_id": "",
            "hypernym": "",
            "definition": "",
        }


def _add_ili_and_lemma(entries: list[dict]) -> None:
    """Add ili and lemma fields from WordNet to each entry in place.

    ili  — Interlingual Lexical Identifier string, e.g. "i12345" (or "").
    lemma — primary WN lemma for the synset (or "" for stubs).

    Args:
        entries: list of entry dicts (mutated in place).
    """
    for e in entries:
        ili = lemma = ""
        sid = e.get("synset_id", "")
        if sid:
            try:
                ss = wn.synset(sid)
                ili = ss.ili or ""
                lemmas = ss.lemmas()
                lemma = lemmas[0] if lemmas else ""
            except wn.Error:
                pass
        e["ili"] = ili
        e["lemma"] = lemma


def _remove_group_hyponyms(entries: list[dict]) -> list[dict]:
    """Within each (name, pos) group, remove synsets that are hyponyms of others.

    For example, if SEABIRD has two candidate synsets A and B where B is a
    hyponym of A (A appears in B's hypernym chain), B is removed — the
    reviewer only needs to consider A.  Stubs (no synset_id) are unaffected.

    Args:
        entries: list of entry dicts; must be called after _add_ili_and_lemma
                 so that synset_ids are finalised.

    Returns:
        Filtered list with intra-group hyponyms removed.
    """
    # Group matched entries by (name, pos)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for e in entries:
        if e["synset_id"]:
            groups[(e["name"], e["pos"])].append(e)

    to_remove: set[tuple] = set()  # (name, pos, synset_id) keys to drop

    for (_name, _pos), group in groups.items():
        if len(group) < 2:
            continue
        synset_ids_in_group = {e["synset_id"] for e in group}

        for e in group:
            sid = e["synset_id"]
            ekey = (e["name"], e["pos"], sid)
            if ekey in to_remove:
                continue
            try:
                ss = wn.synset(sid)
            except wn.Error:
                continue

            # Walk hypernym chain; if any ancestor is also in this group → sid is a hyponym
            visited: set[str] = set()
            queue: deque = deque([ss])
            while queue:
                cur = queue.popleft()
                if cur.id in visited:
                    continue
                visited.add(cur.id)
                if len(visited) > 300:
                    break
                if cur.id != sid and cur.id in synset_ids_in_group:
                    to_remove.add(ekey)
                    break
                for h in cur.hypernyms():
                    if h.id not in visited:
                        queue.append(h)

    return [
        e for e in entries if (e["name"], e["pos"], e["synset_id"]) not in to_remove
    ]


def _sort_key(entry: dict) -> tuple:
    """Sort by POS order then name; unknown POS sorts last."""
    return (
        _POS_ORDER.get(entry.get("pos", ""), 99),
        entry.get("name", ""),
    )


def _write_toml(entries: list[dict], path: Path) -> None:
    """Write entries to a TOML file with use = false on each entry.

    Args:
        entries: sorted list of domain entry dicts.
        path:    output file path.
    """
    lines = [
        "# Conceptual metaphor domain map — merged source + target, for manual review",
        "# Generated by scripts/merge_domain_maps.py",
        "#",
        "# Fields:",
        "#   name       — domain name from the thesaurus",
        "#   pos        — part of speech: n, v, a, r  (empty = not yet resolved)",
        "#   ili        — Interlingual Lexical Identifier  (empty if none)",
        "#   synset_id  — canonical WordNet synset    (empty = not yet matched)",
        "#   lemma      — primary WN lemma (if different from name, check the match)",
        "#   hypernym   — parent domain from auto-build (informational only)",
        "#   definition — synset gloss",
        "#   use        — set to true once this entry has been approved for use",
        "",
    ]
    for e in entries:
        lines.append("[[domains]]")
        lines.append(f'name       = "{_toml_str(e["name"])}"')
        lines.append(f'pos        = "{_toml_str(e["pos"])}"')
        lines.append(f'ili        = "{_toml_str(e.get("ili", ""))}"')
        lines.append(f'synset_id  = "{_toml_str(e["synset_id"])}"')
        lines.append(f'lemma      = "{_toml_str(e.get("lemma", ""))}"')
        lines.append(f'hypernym   = "{_toml_str(e["hypernym"])}"')
        lines.append(f'definition = "{_toml_str(e["definition"])}"')
        lines.append("use        = false")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    """Entry point: merge maps, enrich with ILIs and lemmas, write domain_map.toml."""
    parser = argparse.ArgumentParser(
        description="Merge source and target domain maps into a single TOML for review.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source",
        default="build/source_domain_map.toml",
        help="Path to source_domain_map.toml (default: build/source_domain_map.toml)",
    )
    parser.add_argument(
        "--target",
        default="build/target_domain_map.toml",
        help="Path to target_domain_map.toml (default: build/target_domain_map.toml)",
    )
    parser.add_argument(
        "--thesaurus",
        default="build/thesaurus.json",
        help="Path to thesaurus.json for missing-domain detection (default: build/thesaurus.json)",
    )
    parser.add_argument(
        "--out",
        default="domain_map.toml",
        help="Output path (default: domain_map.toml)",
    )
    args = parser.parse_args()

    _setup_wn()

    src_entries = _load_entries(Path(args.source))
    tgt_entries = _load_entries(Path(args.target))
    merged = _merge_maps(src_entries, tgt_entries)
    merged = _remove_parent_nodes(merged)

    thesaurus = json.loads(Path(args.thesaurus).read_text())
    all_names: set[str] = set(thesaurus["domains"]["sources"]) | set(
        thesaurus["domains"]["targets"]
    )
    _add_missing_stubs(merged, all_names)

    entries = sorted(merged.values(), key=_sort_key)
    _clear_self_hypernyms(entries)

    print("Looking up ILIs and lemmas ...", flush=True)
    _add_ili_and_lemma(entries)

    print("Removing within-group hyponyms ...", flush=True)
    entries = _remove_group_hyponyms(entries)

    out_path = Path(args.out)
    _write_toml(entries, out_path)

    n_with_synset = sum(1 for e in entries if e["synset_id"])
    n_with_ili = sum(1 for e in entries if e.get("ili"))
    n_stubs = sum(1 for e in entries if not e["synset_id"])
    print(f"Wrote {len(entries)} entries to {out_path}")
    print(
        f"  {n_with_synset} with synset_id  |  {n_with_ili} with ILI"
        f"  |  {n_stubs} stubs (no synset)"
    )


if __name__ == "__main__":
    main()
