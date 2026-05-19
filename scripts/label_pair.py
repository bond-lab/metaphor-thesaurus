#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["wn>=1.1"]
# ///
"""Assign conceptual metaphor labels to linked WordNet sense pairs.

Given a source (literal) synset and a target (metaphorical) synset, returns
the best available label in the form TARGET IS SOURCE (e.g. COLOUR IS MINERAL),
with a confidence score grounded in attestations from the metaphor thesaurus.

Three quality tiers:
    1 — exact (source, target) pair found in thesaurus_wn.json; label = attested theme name
    2 — both domains resolved from domain maps; label = "TARGET IS SOURCE"
    3 — one domain resolved; other filled from synset lemma (capped confidence)

Usage (CLI):
    python scripts/label_pair.py SOURCE_ID TARGET_ID [--build-dir build] [--json]

    SOURCE_ID : synset ID for the literal/source sense  (e.g. 15019483-n)
    TARGET_ID : synset ID for the metaphorical/target sense (e.g. 04969242-n)

Usage (library):
    from scripts.label_pair import MetaphorLabeler
    labeler = MetaphorLabeler.from_build("build")
    result  = labeler.label("15019483-n", "04969242-n")
"""

import argparse
import json
import re
import tomllib
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

import wn

# ---------------------------------------------------------------------------
# WordNet setup (mirrors wordnet_match.py)
# ---------------------------------------------------------------------------

_BUILD_WN_DATA = Path(__file__).parent.parent / "build" / "wn-data"
_OMW_EN_SPECIFIER = "omw-en:2.0"


def setup_wn() -> None:
    """Point wn at the local build/wn-data directory and download if needed."""
    _BUILD_WN_DATA.mkdir(parents=True, exist_ok=True)
    wn.config.data_directory = _BUILD_WN_DATA
    existing = {lx.specifier() for lx in wn.lexicons()}
    if _OMW_EN_SPECIFIER not in existing:
        wn.download(_OMW_EN_SPECIFIER)


# ---------------------------------------------------------------------------
# Hypernym traversal (copied from wordnet_match.py)
# ---------------------------------------------------------------------------


def _hypernym_matches(synset, domain_words: list[str], max_visited: int = 300) -> dict:
    """BFS over hypernym closure to find domain-word matches in ancestor lemmas.

    Returns:
        {
          "score":   float,   # fraction of domain_words matched
          "matched": {DOMAIN: {"synset_id": ..., "definition": ..., "lemmas": [...]}}
        }
    """
    if not domain_words:
        return {"score": 0.0, "matched": {}}

    targets = {w.lower() for w in domain_words}
    remaining = set(targets)
    matched: dict[str, dict] = {}
    visited: set[str] = set()
    queue: deque = deque([synset])

    while queue and remaining:
        ss = queue.popleft()
        if ss.id in visited:
            continue
        visited.add(ss.id)
        if len(visited) > max_visited:
            break

        lemmas = {lem.lower().replace("_", " ") for lem in ss.lemmas()}
        for dom in list(remaining):
            if any(dom == lem or dom in lem.split() for lem in lemmas):
                matched[dom.upper()] = {
                    "synset_id": ss.id,
                    "definition": ss.definition() or "",
                    "lemmas": ss.lemmas(),
                }
                remaining.discard(dom)

        for h in ss.hypernyms():
            if h.id not in visited:
                queue.append(h)

    score = len(matched) / len(targets) if targets else 0.0
    return {"score": round(score, 3), "matched": matched}


def _extract_domains(theme_name: str) -> tuple[list[str], list[str]]:
    """Parse 'TARGET IS SOURCE' or 'T1/T2 IS S1/S2' into (targets, sources)."""
    parts = re.split(r"\bIS\b", theme_name, maxsplit=1)
    if len(parts) != 2:
        return [theme_name.strip()], []
    target_str, source_str = parts
    targets = [t.strip() for t in target_str.split("/") if t.strip()]
    sources = [s.strip() for s in source_str.split("/") if s.strip()]
    return targets, sources


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class LabelResult:
    """Conceptual metaphor label assigned to a (source, target) synset pair.

    Attributes:
        label:         Metaphor label, e.g. "COLOUR IS MINERAL".
        tier:          Quality tier (1 = exact thesaurus match, 2 = domain maps,
                       3 = partial / fallback).
        confidence:    Attestation-based confidence in [0, 1].
        source_domain: Source domain label, e.g. "MINERAL".
        target_domain: Target domain label, e.g. "COLOUR".
        evidence:      Grounding details (anchors, matched synsets, entry refs).
    """

    label: str
    tier: int
    confidence: float
    source_domain: str
    target_domain: str
    evidence: dict


# ---------------------------------------------------------------------------
# MetaphorLabeler
# ---------------------------------------------------------------------------


class MetaphorLabeler:
    """Assign conceptual metaphor labels to (source, target) WordNet sense pairs.

    Loads up to three build artefacts:
        thesaurus_wn.json      — enriched thesaurus with WN sense links (Tier 1)
        source_domain_map.toml — canonical synsets for source domains (Tier 2)
        target_domain_map.toml — canonical synsets for target domains (Tier 2)
        thesaurus.json         — raw thesaurus for fallback domain word lists

    Missing files degrade gracefully: absent TOML maps fall back to lemma-based
    hypernym matching; absent thesaurus_wn.json skips Tier 1.

    Args:
        thesaurus_wn_path: path to build/thesaurus_wn.json (optional)
        source_map_path:   path to build/source_domain_map.toml (optional)
        target_map_path:   path to build/target_domain_map.toml (optional)
        thesaurus_path:    path to build/thesaurus.json (optional)
    """

    def __init__(
        self,
        thesaurus_wn_path: Path | None,
        source_map_path: Path | None,
        target_map_path: Path | None,
        thesaurus_path: Path | None,
    ) -> None:
        setup_wn()

        # Pair index: (source_id, target_id) → list of entry dicts
        self._pair_index: dict[tuple[str, str], list[dict]] = {}
        # Theme → list of entry dicts (for attestation confidence)
        self._theme_index: dict[str, list[dict]] = {}

        if thesaurus_wn_path and thesaurus_wn_path.exists():
            self._load_thesaurus_wn(thesaurus_wn_path)

        # Reverse domain maps: synset_id → domain_name (grouping-node entries excluded)
        self._source_synset_map: dict[str, str] = {}
        self._target_synset_map: dict[str, str] = {}

        # Fallback domain word lists for lemma-based hypernym matching
        self._source_domain_words: list[str] = []
        self._target_domain_words: list[str] = []

        if source_map_path and source_map_path.exists():
            self._source_synset_map = self._load_toml_reverse_map(source_map_path)
            self._source_domain_words = list(
                dict.fromkeys(self._source_synset_map.values())
            )
        if target_map_path and target_map_path.exists():
            self._target_synset_map = self._load_toml_reverse_map(target_map_path)
            self._target_domain_words = list(
                dict.fromkeys(self._target_synset_map.values())
            )

        if thesaurus_path and thesaurus_path.exists():
            base = json.loads(thesaurus_path.read_text())
            if not self._source_domain_words:
                self._source_domain_words = base["domains"].get("sources", [])
            if not self._target_domain_words:
                self._target_domain_words = base["domains"].get("targets", [])

    @classmethod
    def from_build(cls, build_dir: Path | str = "build") -> "MetaphorLabeler":
        """Construct a labeler from a standard build/ directory.

        Args:
            build_dir: path to the build output directory (default: "build").

        Returns:
            MetaphorLabeler ready to label synset pairs.
        """
        d = Path(build_dir)
        return cls(
            thesaurus_wn_path=d / "thesaurus_wn.json",
            source_map_path=d / "source_domain_map.toml",
            target_map_path=d / "target_domain_map.toml",
            thesaurus_path=d / "thesaurus.json",
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def label(self, source_id: str, target_id: str) -> LabelResult:
        """Assign the best metaphor label to a (source, target) synset pair.

        Args:
            source_id: synset ID for the literal/source sense.
                       Accepts short form ("15019483-n") or full form
                       ("omw-en-15019483-n").
            target_id: synset ID for the metaphorical/target sense.

        Returns:
            LabelResult with label, tier, confidence, and grounding evidence.
        """
        src_id = self._normalise_id(source_id)
        tgt_id = self._normalise_id(target_id)

        result = self._tier1(src_id, tgt_id)
        if result:
            return result

        src_ss = self._lookup_synset(src_id)
        tgt_ss = self._lookup_synset(tgt_id)

        result = self._tier2(src_id, tgt_id, src_ss, tgt_ss)
        if result:
            return result

        return self._tier3(src_id, tgt_id, src_ss, tgt_ss)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_thesaurus_wn(self, path: Path) -> None:
        """Build pair_index and theme_index from thesaurus_wn.json."""
        data = json.loads(path.read_text())
        for part in data["parts"]:
            for theme in part["themes"]:
                theme_name = theme["name"]
                for sub in theme["subsections"]:
                    for entry in sub["entries"]:
                        lit = entry.get("wn_literal")
                        meta = entry.get("wn_metaphorical")
                        if not lit or not meta:
                            continue
                        record = {
                            "theme": theme_name,
                            "headword": entry.get("headword", ""),
                            "literal": lit,
                            "metaphorical": meta,
                        }
                        key = (lit["synset_id"], meta["synset_id"])
                        self._pair_index.setdefault(key, []).append(record)
                        self._theme_index.setdefault(theme_name, []).append(record)

    @staticmethod
    def _load_toml_reverse_map(path: Path) -> dict[str, str]:
        """Parse a domain map TOML into {synset_id: domain_name}.

        Split domain names ("BIRD-1", "BIRD-2") are normalised back to the base
        name ("BIRD") so that labels read naturally.  Grouping nodes and POS roots
        (empty synset_id) are excluded.
        """
        with path.open("rb") as f:
            data = tomllib.load(f)
        result: dict[str, str] = {}
        for entry in data.get("domains", []):
            sid = entry.get("synset_id", "")
            name = entry.get("name", "")
            if not sid or not name:
                continue
            # Normalise "BIRD-1" → "BIRD"
            normalised = re.sub(r"-\d+$", "", name)
            result[sid] = normalised
        return result

    # ------------------------------------------------------------------
    # ID normalisation
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_id(synset_id: str) -> str:
        """Normalise short or full-form synset IDs to 'omw-en-OFFSET-POS'.

        Args:
            synset_id: e.g. "15019483-n" or "omw-en-15019483-n".

        Returns:
            Normalised ID string.
        """
        sid = synset_id.strip()
        if sid.startswith("omw-en-"):
            return sid
        m = re.fullmatch(r"(\d+)-?([nvarstq])", sid)
        if m:
            return f"omw-en-{m.group(1)}-{m.group(2)}"
        return sid

    # ------------------------------------------------------------------
    # Synset lookup
    # ------------------------------------------------------------------

    @staticmethod
    def _lookup_synset(synset_id: str):
        """Return the wn.Synset for a normalised ID, or None if not found."""
        try:
            return wn.synset(synset_id)
        except wn.Error:
            return None

    # ------------------------------------------------------------------
    # Tier 1 — exact pair lookup
    # ------------------------------------------------------------------

    def _tier1(self, src_id: str, tgt_id: str) -> LabelResult | None:
        """Return Tier-1 result if (src_id, tgt_id) is attested in thesaurus_wn.json."""
        entries = self._pair_index.get((src_id, tgt_id))
        if not entries:
            return None

        best = max(
            entries,
            key=lambda e: min(
                e["literal"]["scores"]["total"],
                e["metaphorical"]["scores"]["total"],
            ),
        )
        confidence = min(
            best["literal"]["scores"]["total"],
            best["metaphorical"]["scores"]["total"],
        )
        targets, sources = _extract_domains(best["theme"])
        return LabelResult(
            label=best["theme"],
            tier=1,
            confidence=round(confidence, 3),
            source_domain="/".join(sources),
            target_domain="/".join(targets),
            evidence={
                "headword": best["headword"],
                "literal_synset": src_id,
                "metaphorical_synset": tgt_id,
                "literal_scores": best["literal"]["scores"],
                "metaphorical_scores": best["metaphorical"]["scores"],
            },
        )

    # ------------------------------------------------------------------
    # Tier 2 — domain map matching
    # ------------------------------------------------------------------

    def _tier2(
        self,
        src_id: str,
        tgt_id: str,
        src_ss,
        tgt_ss,
    ) -> LabelResult | None:
        """Return Tier-2 result when both domains resolve from the domain maps."""
        if src_ss is None or tgt_ss is None:
            return None

        src_domain, src_ev = self._resolve_domain(
            src_ss, self._source_synset_map, self._source_domain_words
        )
        tgt_domain, tgt_ev = self._resolve_domain(
            tgt_ss, self._target_synset_map, self._target_domain_words
        )

        if not src_domain or not tgt_domain:
            return None

        label = f"{tgt_domain} IS {src_domain}"
        confidence = self._attested_confidence(label, src_id, tgt_id)
        return LabelResult(
            label=label,
            tier=2,
            confidence=round(confidence, 3),
            source_domain=src_domain,
            target_domain=tgt_domain,
            evidence={"source": src_ev, "target": tgt_ev},
        )

    def _resolve_domain(
        self,
        synset,
        canonical_map: dict[str, str],
        domain_words: list[str],
    ) -> tuple[str, dict]:
        """Find the best-matching thesaurus domain for a synset.

        Strategy A (preferred): walk the synset's hypernym chain; return the
        domain name for the first (shallowest) ancestor found in canonical_map.

        Strategy B (fallback): lemma-based BFS via _hypernym_matches() against
        all known domain word strings.

        Args:
            synset:        wn.Synset to classify.
            canonical_map: {synset_id: domain_name} from the domain map TOML.
            domain_words:  list of domain name strings for lemma matching.

        Returns:
            (domain_name, evidence_dict) or ("", {}) if nothing matched.
        """
        # Strategy A — canonical synset ancestry
        if canonical_map:
            visited: set[str] = set()
            queue: deque = deque([synset])
            while queue:
                ss = queue.popleft()
                if ss.id in visited:
                    continue
                visited.add(ss.id)
                if len(visited) > 300:
                    break
                if ss.id in canonical_map:
                    return canonical_map[ss.id], {
                        "match_type": "canonical_synset",
                        "anchor_synset": ss.id,
                        "anchor_definition": ss.definition() or "",
                    }
                for h in ss.hypernyms():
                    if h.id not in visited:
                        queue.append(h)

        # Strategy B — lemma-based fallback
        if domain_words:
            res = _hypernym_matches(synset, domain_words)
            if res["matched"]:
                domain = next(iter(res["matched"]))
                return domain, {
                    "match_type": "lemma",
                    "hyper_matched": res["matched"],
                }

        return "", {}

    # ------------------------------------------------------------------
    # Tier 3 — partial match
    # ------------------------------------------------------------------

    def _tier3(
        self,
        src_id: str,
        tgt_id: str,
        src_ss,
        tgt_ss,
    ) -> LabelResult:
        """Return Tier-3 result using at least one thesaurus domain, or bare lemmas."""
        src_domain = tgt_domain = ""
        src_ev: dict = {}
        tgt_ev: dict = {}

        if src_ss is not None:
            src_domain, src_ev = self._resolve_domain(
                src_ss, self._source_synset_map, self._source_domain_words
            )
        if tgt_ss is not None:
            tgt_domain, tgt_ev = self._resolve_domain(
                tgt_ss, self._target_synset_map, self._target_domain_words
            )

        if not src_domain:
            src_domain = (
                src_ss.lemmas()[0].upper().replace("_", " ")
                if src_ss
                else src_id.upper()
            )
        if not tgt_domain:
            tgt_domain = (
                tgt_ss.lemmas()[0].upper().replace("_", " ")
                if tgt_ss
                else tgt_id.upper()
            )

        label = f"{tgt_domain} IS {src_domain}"
        # Cap at 0.5: one or both sides came from a lemma fallback
        confidence = min(0.5, self._attested_confidence(label, src_id, tgt_id))
        return LabelResult(
            label=label,
            tier=3,
            confidence=round(confidence, 3),
            source_domain=src_domain,
            target_domain=tgt_domain,
            evidence={"source": src_ev, "target": tgt_ev},
        )

    # ------------------------------------------------------------------
    # Attestation-based confidence
    # ------------------------------------------------------------------

    def _attested_confidence(self, label: str, src_id: str, tgt_id: str) -> float:
        """Confidence based on proximity to attested synsets for this label.

        For each role (source and target):
          1.0 — synset is in the attested pool for this theme
          0.5 — synset is a hyponym of an attested synset (more specific)
          0.5 — synset is a hypernym of an attested synset (more general)
          0.0 — no relationship found

        Final confidence = 0.5 × (source_proximity + target_proximity).

        If the label has no attested entries in thesaurus_wn.json → 0.1.

        Args:
            label:  candidate metaphor label, e.g. "COLOUR IS MINERAL".
            src_id: normalised source synset ID.
            tgt_id: normalised target synset ID.

        Returns:
            Confidence score in [0, 1].
        """
        entries = self._theme_index.get(label, [])
        if not entries:
            return 0.1

        attested_src = {e["literal"]["synset_id"] for e in entries}
        attested_tgt = {e["metaphorical"]["synset_id"] for e in entries}

        src_prox = self._synset_proximity(src_id, attested_src)
        tgt_prox = self._synset_proximity(tgt_id, attested_tgt)
        return round(0.5 * (src_prox + tgt_prox), 3)

    def _synset_proximity(self, query_id: str, attested: set[str]) -> float:
        """Proximity of query_id to a set of attested synset IDs.

        Args:
            query_id: normalised synset ID to test.
            attested: set of attested synset IDs for the relevant role.

        Returns:
            1.0 (exact match), 0.5 (related via hypernym chain), or 0.0.
        """
        if query_id in attested:
            return 1.0

        query_ss = self._lookup_synset(query_id)
        if query_ss is None:
            return 0.0

        # Is query_ss a hyponym of an attested synset?
        # Walk query's hypernym chain; if any ancestor is attested → query is more specific.
        visited: set[str] = set()
        queue: deque = deque([query_ss])
        while queue:
            ss = queue.popleft()
            if ss.id in visited:
                continue
            visited.add(ss.id)
            if len(visited) > 300:
                break
            if ss.id != query_id and ss.id in attested:
                return 0.5
            for h in ss.hypernyms():
                if h.id not in visited:
                    queue.append(h)

        # Is query_ss a hypernym of an attested synset?
        # Walk each attested synset's hypernym chain; if query appears → query is more general.
        for att_id in attested:
            att_ss = self._lookup_synset(att_id)
            if att_ss is None:
                continue
            visited2: set[str] = set()
            queue2: deque = deque([att_ss])
            while queue2:
                ss = queue2.popleft()
                if ss.id in visited2:
                    continue
                visited2.add(ss.id)
                if len(visited2) > 300:
                    break
                if ss.id == query_id:
                    return 0.5
                for h in ss.hypernyms():
                    if h.id not in visited2:
                        queue2.append(h)

        return 0.0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _format_result(result: LabelResult) -> str:
    """Format a LabelResult for human-readable terminal output."""
    lines = [
        f"{result.label}  [tier={result.tier}, confidence={result.confidence:.3f}]"
    ]
    src_ev = result.evidence.get("source", {})
    tgt_ev = result.evidence.get("target", {})
    if src_ev.get("anchor_synset"):
        lines.append(
            f"  source: {result.source_domain} → {src_ev['anchor_synset']}"
            f" ({src_ev['anchor_definition']})"
        )
    if tgt_ev.get("anchor_synset"):
        lines.append(
            f"  target: {result.target_domain} → {tgt_ev['anchor_synset']}"
            f" ({tgt_ev['anchor_definition']})"
        )
    hw = result.evidence.get("headword")
    if hw:
        lines.append(f"  entry:  {hw}")
    return "\n".join(lines)


def main() -> None:
    """Entry point for CLI use."""
    parser = argparse.ArgumentParser(
        description=(
            "Assign a conceptual metaphor label to a (source, target) WordNet sense pair.\n\n"
            "SOURCE_ID is the literal/source synset; TARGET_ID is the metaphorical/target synset."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("source_id", help="Literal/source synset ID (e.g. 15019483-n)")
    parser.add_argument(
        "target_id", help="Metaphorical/target synset ID (e.g. 04969242-n)"
    )
    parser.add_argument(
        "--build-dir",
        default="build",
        help="Path to build/ directory containing thesaurus artefacts (default: build)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of human-readable text",
    )
    args = parser.parse_args()

    labeler = MetaphorLabeler.from_build(args.build_dir)
    result = labeler.label(args.source_id, args.target_id)

    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(_format_result(result))


if __name__ == "__main__":
    main()
