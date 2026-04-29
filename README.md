# metaphor-thesaurus

Link a conceptual metaphor thesaurus to WordNet senses, using definition
similarity and hypernym chain matching.

**Book:** <https://www.benjamins.com/catalog/hcp.78>
**Thesaurus (supplementary material):** <https://www.benjamins.com/catalog/hcp.78/additional>

Suggested as a useful source by Fatma BENELHADJ, Faculty of Arts and Humanities, University of Sfax.

---

## Quick start

```bash
# Prerequisites: uv (https://docs.astral.sh/uv/) and Ollama (for embeddings)
./build.sh
```

`build.sh` will:
1. Create a `.venv` and install dependencies from `requirements.txt`
2. Download WordNet data (`omw-en:2.0`) if not already cached
3. Parse `THE_THESAURUS.docx` → `build/thesaurus.json`
4. Run tests (`tests/test_wordnet_match.py`)
5. Match every entry to WordNet senses → `build/thesaurus_wn.json`
6. Analyse domain-to-synset consistency → `build/domain_analysis.txt`
7. Build source, target, and combined domain maps → `build/*_domain_map.toml`
8. Compare source vs target maps → `build/domain_map_comparison.txt`

### Options

| Flag | Effect |
|---|---|
| `--overlap` | Use Jaccard bag-of-words instead of embeddings |
| `--simcse` | Also run a SimCSE pass → `build/thesaurus_wn_simcse.json` |
| `--limit N` | Smoke-test: process only first N entries, write to `build/thesaurus_wn_test.json` |

```bash
./build.sh --overlap            # fast, no Ollama needed
./build.sh --simcse             # also run SimCSE embeddings pass
./build.sh --limit 50           # quick smoke-test
./build.sh --limit 50 --overlap # quick smoke-test without Ollama
```

---

## Repository layout

```
build.sh                 pipeline entry point
requirements.txt         Python dependencies

scripts/
  extract.py             parse THE_THESAURUS.docx → build/thesaurus.json
  wordnet_match.py       match entries to WordNet senses → build/thesaurus_wn.json
  analyse_domains.py     domain consistency analysis → build/domain_analysis.txt
  build_domain_map.py    domain → canonical WordNet synset TOML map (--role source/target/combined)
  compare_domain_maps.py compare source vs target maps, flag synset divergences

tests/
  test_wordnet_match.py  unit + integration tests

paper/
  paper.tex              LaTeX write-up
  paper.bib              bibliography

build/                   generated artefacts (not committed)
  thesaurus.json         extracted thesaurus
  thesaurus_wn.json      thesaurus enriched with WordNet sense matches
  domain_analysis.txt    domain → synset consistency table
  source_domain_map.toml   source domain → canonical WordNet synset (human-editable)
  target_domain_map.toml   target domain → canonical WordNet synset
  combined_domain_map.toml combined evidence from both roles
  domain_map_comparison.txt source vs target synset divergence report
  embeddings_cache.json    persistent embedding cache (Ollama + SimCSE, keyed by model)
```

---

## Matching approach

Each thesaurus entry has:
- a **literal meaning** (short gloss, e.g. *transparent bright blue precious stone*)
- a **metaphorical meaning** (e.g. *BRIGHT COLOUR*)
- a **theme** of the form `TARGET IS SOURCE` (e.g. `COLOUR IS MINERAL`)

For each entry the pipeline finds the best WordNet sense for the literal
meaning and (independently) for the metaphorical meaning, using two signals:

1. **Definition similarity** — overlap (Jaccard) or cosine similarity of
   dense embeddings (`embeddinggemma` via Ollama) between the thesaurus
   gloss and the WordNet definition + examples.

2. **Hypernym chain match** — BFS over the WordNet hypernym closure:
   - *literal sense* is matched against **SOURCE** domain words
   - *metaphorical sense* is matched against **TARGET** domain words

   Records the specific ancestor synset that matched, e.g.
   `MINERAL → omw-en-14662574-n`.

The two signals are combined: `score = α·sim + (1−α)·hypernym_score` (default α = 0.5).

### Demo

```bash
.venv/bin/python scripts/wordnet_match.py --headword sapphire
.venv/bin/python scripts/wordnet_match.py --headword sapphire --method embeddings
```

---

## `build/thesaurus_wn.json` structure

Extends `build/thesaurus.json` with four new fields on each entry:

| Field | Description |
|---|---|
| `wn_n_senses` | Number of WordNet synsets found for this headword |
| `wn_method` | Scoring method used (`overlap` or `embeddings`) |
| `wn_literal` | Best synset match for the literal meaning (see below) |
| `wn_metaphorical` | Best synset match for the metaphorical meaning |

Each `wn_literal` / `wn_metaphorical` object:

```json
{
  "synset_id":  "omw-en-15019483-n",
  "pos":        "n",
  "definition": "a precious transparent stone of rich blue corundum …",
  "examples":   [],
  "lemmas":     ["sapphire"],
  "scores": {
    "def_score":     0.444,
    "hyper_score":   1.0,
    "hyper_matched": {
      "MINERAL": {
        "synset_id":  "omw-en-14662574-n",
        "definition": "solid homogeneous inorganic substances …",
        "lemmas":     ["mineral"]
      }
    },
    "total": 0.722
  }
}
```

---

## `build/thesaurus.json` structure

```json
{
  "domains": {
    "targets": ["ACTIVITY", "EMOTION", "..."],
    "sources": ["ANIMAL", "BUILDING", "..."]
  },
  "parts": [
    {
      "name": "Part 1   Values, Qualities And Quantities",
      "themes": [
        {
          "name": "QUALITY IS MONEY/WEALTH",
          "relationships": [
            { "symbol": "#", "theme": "BAD/UNIMPORTANT IS POOR/CHEAP" },
            { "symbol": "#", "theme": "HUMAN IS VALUABLE OBJECT/COMMODITY" }
          ],
          "subsections": [
            {
              "heading": "Positive qualities are wealth and money",
              "entries": [
                {
                  "headword": "wealth",
                  "reversal_prefix": "",
                  "literal_meaning": "large amount of money",
                  "word_class_literal": "n",
                  "word_class_metaphorical": "n",
                  "metaphorical_meaning": "LARGE AMOUNT OF DESIRABLE THINGS",
                  "example": "he uses a wealth of effective teaching techniques"
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

### Entry fields

| Field | Description |
|---|---|
| `headword` | The lexical item (bold in source) |
| `reversal_prefix` | `">>"` if marked as a reversal, else `""` |
| `literal_meaning` | Short gloss of the literal sense |
| `word_class_literal` | POS in literal use, e.g. `n`, `adj` |
| `word_class_metaphorical` | POS in metaphorical use, e.g. `vt`, `idi(vt+adv)` |
| `metaphorical_meaning` | Abstract metaphorical meaning (uppercase) |
| `example` | Illustrative sentence |

### Relationship symbols

`<` part-of · `>` includes · `#` converse · `>>` reversal · `⇔` related · `^` subset · `v` superordinate

### Word class abbreviations

`adj` adjective · `adjphr` adjective phrase · `adv` adverb · `advphr` adverbial phrase · `art` article · `cl` clause · `excl` exclamation · `idi` idiom · `n` noun · `nplur` plural noun · `nphr` noun phrase · `pr` preposition · `pref` prefix · `prphr` prepositional phrase · `pt` particle · `v` verb · `verg` ergative verb · `vi` intransitive verb · `v-inf` infinitive · `virec` reciprocal verb · `vtref` reflexive verb · `vt` transitive verb · `prp` present participle · `pp` past participle
