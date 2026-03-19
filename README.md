# metaphor-thesaurus
Try to link the metaphor thesaurus to wordnet


This is the book:

<https://www.benjamins.com/catalog/hcp.78>

This is the thesaurus:

<https://www.benjamins.com/catalog/hcp.78/additional>

Suggested as a useful source by Fatma BENELHADJ, Faculty of Arts and Humanities, University of Sfax

## Extraction

Run `uv run extract.py` to parse `THE_THESAURUS.docx` into `thesaurus.json`.

## JSON structure

```json
{
  "domains": {
    "targets": ["ACTIVITY", "EMOTION", ...],
    "sources": ["ANIMAL", "BUILDING", ...]
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

### Fields

| Field | Description |
|---|---|
| `domains.targets` | Sorted list of all unique target domains (left side of IS) across all themes |
| `domains.sources` | Sorted list of all unique source domains (right side of IS) across all themes |
| `parts[].name` | Part heading, e.g. `Part 1   Values, Qualities And Quantities` |
| `themes[].name` | Metaphor theme in the form `TARGET IS SOURCE`, e.g. `QUALITY IS MONEY/WEALTH` |
| `themes[].relationships` | Related themes, each with a `symbol` and `theme` name |
| `relationships[].symbol` | Semantic relationship: `<` part-of, `>` includes, `#` converse, `>>` reversal, `⇔` related, `^` subset, `v` superordinate |
| `subsections[].heading` | Underlined sub-grouping heading within a theme (empty string if none) |
| `entries[].headword` | The lexical item (bold, lowercase in source) |
| `entries[].reversal_prefix` | `">>"` if entry is marked as a reversal in the source, else `""` |
| `entries[].literal_meaning` | Literal English meaning of the headword |
| `entries[].word_class_literal` | Word class in literal use, e.g. `n`, `adj`, `(n)` |
| `entries[].word_class_metaphorical` | Word class in metaphorical use, e.g. `vt`, `idi(vt+adv)` |
| `entries[].metaphorical_meaning` | Metaphorical meaning in uppercase |
| `entries[].example` | Example sentence in italics in the source |

### Word class abbreviations

`adj` adjective · `adjphr` adjective phrase · `adv` adverb · `advphr` adverbial phrase · `art` article · `cl` clause · `excl` exclamation · `idi` idiom · `n` noun · `nplur` plural noun · `nphr` noun phrase · `pr` preposition · `pref` prefix · `prphr` prepositional phrase · `pt` particle · `v` verb · `verg` ergative verb · `vi` intransitive verb · `v-inf` infinitive · `virec` reciprocal verb · `vtref` reflexive verb · `vt` transitive verb · `prp` present participle · `pp` past participle
