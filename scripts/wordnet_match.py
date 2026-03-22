#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["wn", "ollama"]
# ///
"""Match thesaurus entries to WordNet senses.

Two scoring signals:
  1. Definition scorer  — how well the WordNet definition matches the meaning.
                          Choose with --method:
                            overlap     Jaccard on content-word bag-of-words
                            embeddings  cosine similarity via Ollama
  2. Hypernym chain     — walks the synset's ancestor chain looking for SOURCE
                          (literal) or TARGET (metaphorical) domain words.
                          Records which synset matched, e.g.
                            MINERAL → omw-en-14662574-n

For embeddings, all unique texts are collected in a first pass, batched to
Ollama in one or more calls, and persisted to a cache file.  Reruns only
embed new texts.

Output: thesaurus_wn.json — a copy of thesaurus.json with wn_literal and
wn_metaphorical added to each entry.

Usage:
  uv run wordnet_match.py --headword sapphire
  uv run wordnet_match.py --method embeddings --headword sapphire
  uv run wordnet_match.py --method embeddings [--resume] [--out thesaurus_wn.json]
"""

import argparse
import copy
import json
import math
import re
import sys
from pathlib import Path
from typing import Callable

import wn

# ---------------------------------------------------------------------------
# WordNet setup
# ---------------------------------------------------------------------------

BUILD_WN_DATA    = Path(__file__).parent.parent / "build" / "wn-data"
OEWN_SPECIFIER   = "ewn:2020"
OMW_EN_SPECIFIER = "omw-en:2.0"


def setup_wn() -> None:
    """Point wn at the local build directory and download if needed."""
    BUILD_WN_DATA.mkdir(parents=True, exist_ok=True)
    wn.config.data_home = str(BUILD_WN_DATA)

    existing = {lx.specifier() for lx in wn.lexicons()}
    for spec in (OMW_EN_SPECIFIER,):
        if spec in existing:
            print(f"  {spec} already loaded", flush=True)
        else:
            print(f"  Downloading {spec} ...", flush=True)
            wn.download(spec)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "of", "in", "on", "at",
    "to", "for", "with", "or", "and", "that", "be", "it", "its", "by",
    "from", "as", "this", "which", "have", "has", "had", "used",
}

WC_TO_POS: dict[str, str] = {
    "n": "n", "nplur": "n", "nphr": "n",
    "adj": "a", "adjphr": "a",
    "adv": "r", "advphr": "r",
    "v": "v", "vi": "v", "vt": "v", "vtref": "v", "virec": "v", "verg": "v",
}

# A scorer takes (synset_text, meaning_text) → similarity in [0, 1]
DefScorer = Callable[[str, str], float]


# ---------------------------------------------------------------------------
# Overlap scorer
# ---------------------------------------------------------------------------

def tokenize(text: str) -> set[str]:
    words = re.findall(r"\b[a-z]+\b", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def overlap_scorer(synset_text: str, meaning: str) -> float:
    return jaccard(tokenize(synset_text), tokenize(meaning))


# ---------------------------------------------------------------------------
# Embedding scorer
# ---------------------------------------------------------------------------

def _cosine(v1: list[float], v2: list[float]) -> float:
    dot   = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    return dot / (norm1 * norm2) if norm1 and norm2 else 0.0


class EmbeddingScorer:
    """Cosine similarity via Ollama embeddings with persistent on-disk cache.

    Workflow:
      1. Call precompute(texts) once with all unique texts before scoring.
         This batches Ollama calls and saves results to cache_path after
         each batch so a crash loses at most one batch.
      2. __call__ then only does dict lookups — no more Ollama calls.
    """

    def __init__(
        self,
        model: str = "embeddinggemma:latest",
        cache_path: Path = Path("embeddings_cache.json"),
        batch_size: int = 64,
    ):
        import ollama as _ollama
        self._ollama    = _ollama
        self.model      = model
        self.cache_path = cache_path
        self.batch_size = batch_size
        self._cache: dict[str, list[float]] = {}
        if cache_path.exists():
            raw = json.loads(cache_path.read_text())
            # Cache is keyed by "model\x00text" to isolate different models
            self._cache = raw.get(model, {})
            print(f"Loaded {len(self._cache)} cached embeddings for {model}")

    def _save(self) -> None:
        # Load the full file (may have other models), update our slice, write back
        if self.cache_path.exists():
            full = json.loads(self.cache_path.read_text())
        else:
            full = {}
        full[self.model] = self._cache
        self.cache_path.write_text(json.dumps(full, separators=(",", ":")))

    def precompute(self, texts: list[str]) -> None:
        """Embed all unique non-empty texts not already in cache, in batches."""
        needed = [t for t in dict.fromkeys(texts) if t and t not in self._cache]
        if not needed:
            print("All texts already cached.")
            return
        print(f"Embedding {len(needed)} new texts "
              f"(batch_size={self.batch_size}, model={self.model}) ...")
        for i in range(0, len(needed), self.batch_size):
            batch = needed[i : i + self.batch_size]
            resp  = self._ollama.embed(model=self.model, input=batch)
            for text, vec in zip(batch, resp.embeddings):
                self._cache[text] = vec
            self._save()
            done = min(i + self.batch_size, len(needed))
            print(f"  {done}/{len(needed)}", end="\r", flush=True)
        print(f"\nDone. Cache now has {len(self._cache)} entries.")

    def _embed(self, text: str) -> list[float]:
        """Single-text fallback (used in --headword demo mode)."""
        if text not in self._cache:
            resp = self._ollama.embed(model=self.model, input=[text])
            self._cache[text] = resp.embeddings[0]
            self._save()
        return self._cache[text]

    def __call__(self, synset_text: str, meaning: str) -> float:
        if not synset_text or not meaning:
            return 0.0
        return _cosine(self._embed(synset_text), self._embed(meaning))


class SimCSEScorer:
    """Cosine similarity via a sentence-transformers SimCSE model.

    Uses the same on-disk cache format as EmbeddingScorer so the two can
    share a cache file without collision (keyed by model name).

    Default model: princeton-nlp/sup-simcse-roberta-large
    Lighter option: princeton-nlp/sup-simcse-bert-base-uncased
    """

    def __init__(
        self,
        model: str = "princeton-nlp/sup-simcse-roberta-large",
        cache_path: Path = Path("build/embeddings_cache.json"),
        batch_size: int = 64,
    ):
        from sentence_transformers import SentenceTransformer
        self.model_name = model
        self.cache_path = cache_path
        self.batch_size = batch_size
        self._st = SentenceTransformer(model)
        self._cache: dict[str, list[float]] = {}
        if cache_path.exists():
            raw = json.loads(cache_path.read_text())
            self._cache = raw.get(model, {})
            print(f"Loaded {len(self._cache)} cached embeddings for {model}")

    def _save(self) -> None:
        full = json.loads(self.cache_path.read_text()) if self.cache_path.exists() else {}
        full[self.model_name] = self._cache
        self.cache_path.write_text(json.dumps(full, separators=(",", ":")))

    def precompute(self, texts: list[str]) -> None:
        needed = [t for t in dict.fromkeys(texts) if t and t not in self._cache]
        if not needed:
            print("All texts already cached.")
            return
        print(f"Embedding {len(needed)} texts with SimCSE "
              f"(batch_size={self.batch_size}, model={self.model_name}) ...")
        for i in range(0, len(needed), self.batch_size):
            batch = needed[i : i + self.batch_size]
            vecs = self._st.encode(batch, show_progress_bar=False)
            for text, vec in zip(batch, vecs):
                self._cache[text] = vec.tolist()
            self._save()
            print(f"  {min(i + self.batch_size, len(needed))}/{len(needed)}",
                  end="\r", flush=True)
        print(f"\nDone. Cache now has {len(self._cache)} entries.")

    def _embed(self, text: str) -> list[float]:
        if text not in self._cache:
            vec = self._st.encode([text], show_progress_bar=False)[0]
            self._cache[text] = vec.tolist()
            self._save()
        return self._cache[text]

    def __call__(self, synset_text: str, meaning: str) -> float:
        if not synset_text or not meaning:
            return 0.0
        return _cosine(self._embed(synset_text), self._embed(meaning))


def make_scorer(
    method: str,
    embedding_model: str,
    cache_path: Path,
    batch_size: int,
) -> DefScorer:
    if method == "overlap":
        return overlap_scorer
    if method == "embeddings":
        return EmbeddingScorer(
            model=embedding_model,
            cache_path=cache_path,
            batch_size=batch_size,
        )
    if method == "simcse":
        return SimCSEScorer(
            model=embedding_model,
            cache_path=cache_path,
            batch_size=batch_size,
        )
    raise ValueError(f"Unknown method: {method!r}")


# ---------------------------------------------------------------------------
# WordNet helpers
# ---------------------------------------------------------------------------

def normalize_wc(wc: str) -> str | None:
    wc = wc.strip().strip("()")
    if wc.startswith("idi") or wc in ("prp", "pr", "pp", "art", "cl", "excl", "pref"):
        return None
    if "|" in wc:
        wc = wc.split("|")[0].strip("() ")
    if "+" in wc:
        wc = wc.split("+")[0]
    return WC_TO_POS.get(wc)


def synset_text(synset) -> str:
    """Concatenate definition and examples into one string."""
    parts = [synset.definition() or ""] + list(synset.examples())
    return " ".join(p for p in parts if p)


def get_synsets(hw: str) -> list:
    return wn.synsets(hw)


# ---------------------------------------------------------------------------
# Hypernym traversal
# ---------------------------------------------------------------------------

def hypernym_matches(synset, domain_words: list[str], max_visited: int = 300) -> dict:
    """
    BFS over hypernym closure.  For each domain word, record the first
    (shallowest from the synset, i.e. most specific) ancestor synset whose
    lemmas contain that word.

    Returns:
        {
          "score":   float,           # fraction of domain_words matched
          "matched": {
              "MINERAL": {
                  "synset_id":  "omw-en-14662574-n",
                  "definition": "…",
                  "lemmas":     ["mineral"],
              }, …
          }
        }
    """
    if not domain_words:
        return {"score": 0.0, "matched": {}}

    targets   = {w.lower() for w in domain_words}
    remaining = set(targets)
    matched: dict[str, dict] = {}
    visited: set[str] = set()
    queue = [synset]

    while queue and remaining:
        ss = queue.pop(0)
        if ss.id in visited:
            continue
        visited.add(ss.id)
        if len(visited) > max_visited:
            break

        lemmas = {lem.lower().replace("_", " ") for lem in ss.lemmas()}
        for dom in list(remaining):
            if any(dom in lem for lem in lemmas):
                matched[dom.upper()] = {
                    "synset_id":  ss.id,
                    "definition": ss.definition() or "",
                    "lemmas":     ss.lemmas(),
                }
                remaining.discard(dom)

        for h in ss.hypernyms():
            if h.id not in visited:
                queue.append(h)

    score = len(matched) / len(targets) if targets else 0.0
    return {"score": round(score, 3), "matched": matched}


# ---------------------------------------------------------------------------
# Per-sense scoring
# ---------------------------------------------------------------------------

def score_sense(
    synset,
    meaning: str,
    domain_words: list[str],
    alpha: float,
    scorer: DefScorer,
) -> dict:
    d     = scorer(synset_text(synset), meaning)
    h_res = hypernym_matches(synset, domain_words)
    return {
        "def_score":     round(d, 3),
        "hyper_score":   h_res["score"],
        "hyper_matched": h_res["matched"],
        "total":         round(alpha * d + (1 - alpha) * h_res["score"], 3),
    }


# ---------------------------------------------------------------------------
# Domain extraction
# ---------------------------------------------------------------------------

def extract_domains(theme_name: str) -> tuple[list[str], list[str]]:
    parts = re.split(r"\bIS\b", theme_name, maxsplit=1)
    if len(parts) != 2:
        return [theme_name.strip()], []
    target_str, source_str = parts
    targets = [t.strip() for t in target_str.split("/") if t.strip()]
    sources = [s.strip() for s in source_str.split("/") if s.strip()]
    return targets, sources


# ---------------------------------------------------------------------------
# Text collection (pre-pass for batch embedding)
# ---------------------------------------------------------------------------

def collect_texts(data: dict, limit: int = 0) -> tuple[list[str], dict[str, list]]:
    """
    Walk the thesaurus and return:
      - all_texts: deduplicated list of strings to embed
        (synset texts + literal meanings + metaphorical meanings)
      - synsets_by_hw: headword → list of synsets (cached to avoid double lookup)
    """
    all_texts: list[str] = []
    seen: set[str] = set()
    synsets_by_hw: dict[str, list] = {}

    def add(text: str) -> None:
        if text and text not in seen:
            seen.add(text)
            all_texts.append(text)

    n = 0
    for part in data["parts"]:
        for theme in part["themes"]:
            for sub in theme["subsections"]:
                for entry in sub["entries"]:
                    if limit and n >= limit:
                        return all_texts, synsets_by_hw
                    n += 1
                    hw = re.sub(r"[(\s]+$", "", entry["headword"]).strip()

                    add(entry.get("literal_meaning", "").strip())
                    add(entry.get("metaphorical_meaning", "").strip())

                    if hw not in synsets_by_hw:
                        synsets_by_hw[hw] = get_synsets(hw)
                    for ss in synsets_by_hw[hw]:
                        add(synset_text(ss))

    return all_texts, synsets_by_hw


# ---------------------------------------------------------------------------
# Entry matching (uses pre-looked-up synsets)
# ---------------------------------------------------------------------------

def match_entry(
    entry: dict,
    theme_name: str,
    synsets: list,
    alpha: float,
    scorer: DefScorer,
    method: str,
) -> dict:
    hw               = re.sub(r"[(\s]+$", "", entry["headword"]).strip()
    literal_meaning  = entry.get("literal_meaning", "").strip()
    metaphor_meaning = entry.get("metaphorical_meaning", "").strip()
    targets, sources = extract_domains(theme_name)

    if not synsets:
        return {
            "headword": hw, "theme": theme_name,
            "sources": sources, "targets": targets,
            "n_senses": 0, "literal": None, "metaphorical": None,
        }

    all_senses = []
    for ss in synsets:
        lit  = score_sense(ss, literal_meaning,  sources, alpha, scorer)
        meta = score_sense(ss, metaphor_meaning, targets, alpha, scorer)
        all_senses.append({
            "synset_id":    ss.id,
            "pos":          "a" if ss.pos == "s" else ss.pos,
            "definition":   ss.definition() or "",
            "examples":     ss.examples(),
            "lemmas":       ss.lemmas(),
            "literal":      lit,
            "metaphorical": meta,
        })

    best_lit  = max(all_senses, key=lambda x: x["literal"]["total"])
    best_meta = max(all_senses, key=lambda x: x["metaphorical"]["total"])

    def summary(row: dict, key: str) -> dict:
        return {
            "synset_id":  row["synset_id"],
            "pos":        row["pos"],
            "definition": row["definition"],
            "examples":   row["examples"],
            "lemmas":     row["lemmas"],
            "scores":     row[key],
        }

    return {
        "headword":             hw,
        "theme":                theme_name,
        "sources":              sources,
        "targets":              targets,
        "n_senses":             len(all_senses),
        "literal_meaning":      literal_meaning,
        "metaphorical_meaning": metaphor_meaning,
        "method":               method,
        "literal":              summary(best_lit,  "literal"),
        "metaphorical":         summary(best_meta, "metaphorical"),
    }


# ---------------------------------------------------------------------------
# Pretty-print (demo mode)
# ---------------------------------------------------------------------------

def print_result(r: dict) -> None:
    print(f"\n{'='*60}")
    print(f"Headword : {r['headword']}")
    print(f"Theme    : {r['theme']}")
    print(f"Sources  : {r['sources']}  (literal domain)")
    print(f"Targets  : {r['targets']}  (metaphor domain)")
    print(f"Method   : {r['method']}")
    print(f"Literal meaning     : {r['literal_meaning']}")
    print(f"Metaphorical meaning: {r['metaphorical_meaning']}")
    print(f"WordNet senses      : {r['n_senses']}")
    if not r["n_senses"]:
        print("  (no WordNet senses found)")
        return
    for label, key in [("LITERAL", "literal"), ("METAPHORICAL", "metaphorical")]:
        best = r[key]
        print(f"\nBest {label} match: [{best['pos']}] {best['synset_id']}")
        print(f"  definition : {best['definition']}")
        print(f"  scores     : {best['scores']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Match thesaurus entries to WordNet senses"
    )
    parser.add_argument("--headword",        help="Only process this headword (demo mode)")
    parser.add_argument("--method",          choices=["overlap", "embeddings", "simcse"],
                        default="overlap",   help="Definition similarity method (default: overlap)")
    parser.add_argument("--embedding-model", default=None,
                        help="Model name for embeddings/simcse "
                             "(default: embeddinggemma:latest for embeddings, "
                             "princeton-nlp/sup-simcse-roberta-large for simcse)")
    parser.add_argument("--batch-size",      type=int, default=64,
                        help="Texts per Ollama embed call (default: 64)")
    parser.add_argument("--cache",           default="build/embeddings_cache.json",
                        help="Persistent embedding cache file")
    parser.add_argument("--alpha",           type=float, default=0.5,
                        help="Weight for definition score vs hypernym score (default: 0.5)")
    parser.add_argument("--out",             default="build/thesaurus_wn.json",
                        help="Output enriched thesaurus JSON")
    parser.add_argument("--thesaurus",       default="build/thesaurus.json")
    parser.add_argument("--resume",          action="store_true",
                        help="Skip entries that already have wn_literal in the output file")
    parser.add_argument("--limit",           type=int, default=0,
                        help="Only process the first N entries (0 = all)")
    args = parser.parse_args()

    # Set default embedding model per method
    if args.embedding_model is None:
        args.embedding_model = (
            "princeton-nlp/sup-simcse-roberta-large" if args.method == "simcse"
            else "embeddinggemma:latest"
        )

    setup_wn()
    data = json.loads(Path(args.thesaurus).read_text())

    # In demo mode, just process one headword without pre-pass overhead
    if args.headword:
        scorer = make_scorer(args.method, args.embedding_model,
                             Path(args.cache), args.batch_size)
        for part in data["parts"]:
            for theme in part["themes"]:
                for sub in theme["subsections"]:
                    for entry in sub["entries"]:
                        hw = re.sub(r"[(\s]+$", "", entry["headword"]).strip()
                        if hw.lower() != args.headword.lower():
                            continue
                        synsets = get_synsets(hw)
                        r = match_entry(entry, theme["name"], synsets,
                                        args.alpha, scorer, args.method)
                        print_result(r)
        return

    # --- Full run ---

    # Load existing output for --resume
    out_path = Path(args.out)
    existing: dict[str, dict] = {}   # headword → wn result (first occurrence)
    if args.resume and out_path.exists():
        prev = json.loads(out_path.read_text())
        for part in prev.get("parts", []):
            for theme in part["themes"]:
                for sub in theme["subsections"]:
                    for entry in sub["entries"]:
                        if "wn_literal" in entry:
                            existing[entry["headword"]] = {
                                "wn_literal":      entry["wn_literal"],
                                "wn_metaphorical": entry["wn_metaphorical"],
                                "wn_n_senses":     entry["wn_n_senses"],
                                "wn_method":       entry["wn_method"],
                            }
        print(f"Resuming: {len(existing)} entries already processed.")

    # Collect all unique texts for batch embedding
    print("Collecting texts and WordNet synsets ...")
    all_texts, synsets_by_hw = collect_texts(data, limit=args.limit)
    print(f"  {len(all_texts)} unique texts, "
          f"{sum(len(v) for v in synsets_by_hw.values())} synsets "
          f"across {len(synsets_by_hw)} headwords")

    scorer = make_scorer(args.method, args.embedding_model,
                         Path(args.cache), args.batch_size)

    # Batch embed if needed
    if args.method in ("embeddings", "simcse"):
        scorer.precompute(all_texts)  # type: ignore[attr-defined]

    # Score pass — enrich a deep copy of the thesaurus
    enriched = copy.deepcopy(data)
    total = done = skipped = unmatched = 0

    for part in enriched["parts"]:
        for theme in part["themes"]:
            theme_name = theme["name"]
            for sub in theme["subsections"]:
                for entry in sub["entries"]:
                    total += 1
                    hw = re.sub(r"[(\s]+$", "", entry["headword"]).strip()

                    if args.limit and done + skipped >= args.limit:
                        continue

                    if hw in existing:
                        entry.update(existing[hw])
                        skipped += 1
                        continue

                    synsets = synsets_by_hw.get(hw, [])
                    r = match_entry(entry, theme_name, synsets,
                                    args.alpha, scorer, args.method)

                    entry["wn_n_senses"]     = r["n_senses"]
                    entry["wn_method"]       = args.method
                    entry["wn_literal"]      = r["literal"]
                    entry["wn_metaphorical"] = r["metaphorical"]

                    if r["n_senses"] == 0:
                        unmatched += 1
                    done += 1

    out_path.write_text(json.dumps(enriched, indent=2, ensure_ascii=False))
    print(f"\nDone. {total} entries total: "
          f"{done} processed, {skipped} resumed, {unmatched} with no WN senses.")
    print(f"Output → {out_path}")


if __name__ == "__main__":
    main()
