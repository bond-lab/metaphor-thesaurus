# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Full pipeline (requires Ollama running with embeddinggemma:latest)
./build.sh

# Fast smoke-test (no Ollama needed, first 50 entries)
./build.sh --limit 50 --overlap

# Run tests only
.venv/bin/python -m pytest tests/ -v

# Run a single test
.venv/bin/python -m pytest tests/test_wordnet_match.py::test_overlap_scorer -v

# Demo: match one headword
.venv/bin/python scripts/wordnet_match.py --headword sapphire
.venv/bin/python scripts/wordnet_match.py --headword sapphire --method embeddings

# Individual pipeline steps
.venv/bin/python scripts/extract.py                        # → thesaurus.json (cwd)
.venv/bin/python scripts/analyse_domains.py --min-count 2
.venv/bin/python scripts/build_domain_map.py --role source
.venv/bin/python scripts/compare_domain_maps.py
```

Dependencies are managed with `uv`. The venv is `.venv/`; `build.sh` creates it automatically.

## External source files

`THE_THESAURUS.docx` and `GUIDE_TO_USING_THE_THESAURUS.docx` live in `external/` which is excluded from git (not redistributable). `build.sh` downloads the thesaurus automatically; the guide must be placed manually. Scripts reference these via `Path(__file__).parent.parent / "external"`.

## `wn` library

**Always invoke the `wn-python` skill before writing or editing code that uses `wn`.** This repo uses `omw-en:2.0` (not the Open English WordNet). WordNet data is downloaded to `build/wn-data/` and `wn.config.data_directory` must be set before any `wn` calls — see `setup_wn()` in `scripts/wordnet_match.py`. Adjective satellite POS `"s"` is normalised to `"a"` throughout.

## Architecture

The pipeline has five stages, each a standalone script:

**1. `scripts/extract.py`** — Parses `THE_THESAURUS.docx` (not redistributable, auto-downloaded from John Benjamins) into `build/thesaurus.json` using run-level formatting analysis (bold = headword, italic = example, all-caps = metaphorical meaning, underline = subsection). Theme names split across consecutive paragraphs are merged. Known truncated theme names are fixed via `THEME_FIXUPS`.

**2. `scripts/wordnet_match.py`** — Enriches `build/thesaurus.json` → `build/thesaurus_wn.json`. For each entry it finds the best WordNet synset for both the literal and metaphorical meanings using two signals combined as `α·def_score + (1−α)·hypernym_score` (default α = 0.5):
- **Definition scorer**: Jaccard overlap (`--method overlap`) or cosine similarity via Ollama embeddings (`--method embeddings`) or SimCSE (`--method simcse`).
- **Hypernym chain**: BFS over WN hypernym closure looking for SOURCE domain words (literal) or TARGET domain words (metaphorical).

Embedding runs do a two-pass approach: collect all unique texts first, batch-embed them all into `build/embeddings_cache.json` (keyed by model name), then score. `--resume` skips already-processed entries. `--limit N` writes to `build/thesaurus_wn_test.json` to avoid overwriting a full run.

**3. `scripts/analyse_domains.py`** — Reads `thesaurus_wn.json`, tallies which WN synsets each domain word matched via hypernym chains, computes LCS (Lowest Common Subsumer) per POS group, and prints a consistency table.

**4. `scripts/build_domain_map.py`** — Produces TOML hierarchy files (`source_domain_map.toml`, `target_domain_map.toml`, `combined_domain_map.toml`). Domains with a single best synset become leaf nodes; multi-synset domains get a grouping node plus numbered children. WN ancestry is used to nest more specific domains under their broader counterpart (e.g. WATERBIRD under BIRD).

**5. `scripts/compare_domain_maps.py`** — Diffs source vs target maps, reporting source-only, target-only, and domains with the same name but differing canonical synsets.

**6. `scripts/make_browser_db.py`** — Reads `build/thesaurus_wn.json` and writes `web/thesaurus.db` (SQLite). `build.sh` then gzips it to `web/thesaurus.db.gz`. Tables: `parts`, `themes`, `relationships`, `subsections`, `entries` (with `lang` column for future multilingual use), `theme_domains`, `wn_matches`, plus FTS5 virtual table `entries_fts`. Relationship labels and POS names live in `web/config.json` (multilingual-ready).

## Browser

```bash
# Build DB (done automatically by build.sh step 7)
.venv/bin/python scripts/make_browser_db.py

# Serve locally (must use HTTP, not file://)
bash web/run.sh          # → http://localhost:8080/

# Browser UI tests (requires playwright)
uv pip install pytest-playwright && playwright install chromium
.venv/bin/python -m pytest tests/test_browser_ui.py -v
```

`web/index.html` is a single-file React 18 + sql.js + Tailwind app with no build step. It fetches and decompresses `thesaurus.db.gz` in the browser via `DecompressionStream`. Use `db.prepare()` (not `db.exec()`) for all parameterised SQL in sql.js — `db.exec()` does not reliably bind parameters for some query types. Tabs: **Browse** (Search / Hierarchy / By Source / By Target modes), **Reference** (relationship symbols + POS tables from `config.json`), **About** (attribution + stats). The WN-links toggle in the header shows/hides the `wn_matches` section in entry detail.

**Tooltips on abbreviations:** every abbreviated label in a web interface (relation symbols, POS codes, scores, etc.) must be wrapped in a `<Tooltip>` component so hovering shows the full name or description. This is a general rule for all web UIs in this project — never show a bare abbreviation without a hover explanation.

**Browser testing:** UI tests use Playwright (see `tests/test_browser_ui.py` and `tests/conftest.py`). The `conftest.py` `browser_server` fixture copies `web/{index.html,config.json,thesaurus.db.gz}` to a temp dir and serves them on port 9877. All new browser features must have a corresponding Playwright test. Use `data-testid` attributes for reliable element selection (not CSS classes or text content).

To add a language: add translation keys to `web/config.json` and insert translated entries into `entries` with the appropriate `lang` value; WN matches from a second-language lexicon go into `wn_matches` with a different `lexicon` value.

## Data flow

```
THE_THESAURUS.docx
    → extract.py → build/thesaurus.json
    → wordnet_match.py → build/thesaurus_wn.json
    → analyse_domains.py → build/domain_analysis.txt
    → build_domain_map.py (×3 roles) → build/*_domain_map.toml
    → compare_domain_maps.py → build/domain_map_comparison.txt
    → make_browser_db.py → web/thesaurus.db(.gz)
```

`build/embeddings_cache.json` is a persistent cross-run cache; it is shared between Ollama and SimCSE scorers, keyed by model name at the top level.

## Word-class (POS) conventions

The official 24 abbreviations are listed in guide section 3 and in `web/config.json`. The thesaurus source also uses non-standard extensions (`vt-pp`, `v-prp`, `viprp`, `vphr`, `conj`, `advcl`) which are added to `WORD_CLASSES` in `extract.py`. The `/` character in a WC string means "or" (`n/adj` = noun or adjective). The `|` separates literal from metaphorical WC in conversions (`(n)|vt+pr` = noun literally, transitive-verb+preposition metaphorically). `_WC_COMPOUND` (defined in extract.py) handles all combinations.

A small residual of typos (`prph`, `prphrr`, `npr`, `prep`, `pr…`, `'of'`, `'with`, `nphr, vi`) remains in the source docx; they appear only in a handful of entries and are documented in `test_guide_coverage.py::test_all_entry_wcs_are_known`.

## Key invariants

- `thesaurus.json` structure: `{domains, parts[{name, themes[{name, relationships, subsections[{heading, entries[...]}]}]}]}`
- Each entry has: `headword`, `reversal_prefix` (`">>"` or `""`), `literal_meaning`, `word_class_literal`, `word_class_metaphorical`, `metaphorical_meaning`, `example`.
- `wordnet_match.py` adds four fields per entry: `wn_n_senses`, `wn_method`, `wn_literal`, `wn_metaphorical` (each with `synset_id`, `pos`, `definition`, `examples`, `lemmas`, `scores`).
- Theme names follow `TARGET IS SOURCE` (e.g. `COLOUR IS MINERAL`); `extract_domains()` splits on `\bIS\b` and `/`.
- The `wn` library raises `wn.Error` for unknown synset IDs — always guard with try/except when doing `wn.synset(sid)` by ID.
