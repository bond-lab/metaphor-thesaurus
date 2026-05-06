#!/usr/bin/env python3
"""Build the SQLite browser database from build/thesaurus_wn.json.

Usage:
  uv run scripts/make_browser_db.py
  uv run scripts/make_browser_db.py --input build/thesaurus_wn.json --out web/thesaurus.db
"""

import argparse
import json
import re
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE parts (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE themes (
    id             INTEGER PRIMARY KEY,
    part_id        INTEGER NOT NULL REFERENCES parts(id),
    name           TEXT NOT NULL,
    target_domains TEXT NOT NULL DEFAULT '[]',
    source_domains TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE relationships (
    id            INTEGER PRIMARY KEY,
    theme_id      INTEGER NOT NULL REFERENCES themes(id),
    symbol        TEXT NOT NULL,
    related_theme TEXT NOT NULL
);

CREATE TABLE subsections (
    id       INTEGER PRIMARY KEY,
    theme_id INTEGER NOT NULL REFERENCES themes(id),
    heading  TEXT NOT NULL,
    position INTEGER NOT NULL
);

CREATE TABLE entries (
    id                   INTEGER PRIMARY KEY,
    subsection_id        INTEGER NOT NULL REFERENCES subsections(id),
    lang                 TEXT NOT NULL DEFAULT 'en',
    headword             TEXT NOT NULL,
    source_lemma         TEXT NOT NULL DEFAULT '',
    reversal_prefix      TEXT NOT NULL DEFAULT '',
    literal_meaning      TEXT NOT NULL DEFAULT '',
    wc_literal           TEXT NOT NULL DEFAULT '',
    wc_metaphorical      TEXT NOT NULL DEFAULT '',
    metaphorical_meaning TEXT NOT NULL DEFAULT '',
    example              TEXT NOT NULL DEFAULT ''
);

CREATE TABLE theme_domains (
    theme_id INTEGER NOT NULL REFERENCES themes(id),
    domain   TEXT NOT NULL,
    role     TEXT NOT NULL
);

CREATE TABLE wn_matches (
    id            INTEGER PRIMARY KEY,
    entry_id      INTEGER NOT NULL REFERENCES entries(id),
    role          TEXT NOT NULL,
    method        TEXT NOT NULL DEFAULT '',
    lexicon       TEXT NOT NULL DEFAULT 'omw-en:2.0',
    synset_id     TEXT NOT NULL,
    pos           TEXT NOT NULL DEFAULT '',
    definition    TEXT NOT NULL DEFAULT '',
    lemmas        TEXT NOT NULL DEFAULT '[]',
    def_score     REAL,
    hyper_score   REAL,
    total         REAL,
    hyper_matched TEXT NOT NULL DEFAULT '{}'
);

CREATE VIRTUAL TABLE entries_fts USING fts5(
    headword,
    source_lemma,
    literal_meaning,
    metaphorical_meaning
);

CREATE INDEX idx_themes_part       ON themes(part_id);
CREATE INDEX idx_subsections_theme ON subsections(theme_id);
CREATE INDEX idx_entries_sub       ON entries(subsection_id);
CREATE INDEX idx_entries_lang      ON entries(lang);
CREATE INDEX idx_entries_headword  ON entries(headword COLLATE NOCASE);
CREATE INDEX idx_wn_entry          ON wn_matches(entry_id);
CREATE INDEX idx_theme_domains     ON theme_domains(domain, role);
CREATE INDEX idx_relationships     ON relationships(theme_id);
"""


# Relationship symbols that can appear embedded mid-string (immediately before
# an uppercase letter) due to the docx source not using comma separators.
_EMBEDDED_SYM_RE = re.compile(r"(>>|⇔|⟺|↔|\^|v)(?=[A-Z])")


def _resplit_relationship(sym: str, theme_text: str) -> list[tuple[str, str]]:
    """Split a compound related-theme string into individual (symbol, name) pairs.

    The docx source sometimes places multiple relationships on one paragraph
    line (e.g. ``v HUMAN IS ||BIRD  v HUMAN IS ||REPTILE``), which extract.py
    cannot split because it only recognises comma separators.  This function
    also strips the ``||`` formatting artifact that appears before sub-theme
    references in the source document.

    Args:
        sym: The leading relationship symbol for the whole string.
        theme_text: The raw related_theme value from the JSON.

    Returns:
        List of ``(symbol, cleaned_theme_name)`` pairs.
    """
    # || is a docx formatting artifact used before sub-theme names; strip it.
    text = theme_text.replace("||", "").strip()

    # Split on embedded relationship symbols immediately preceding uppercase.
    # re.split with a capturing group interleaves separators into the parts list:
    # "HUMAN IS BIRD vHUMAN IS REPTILE" → ["HUMAN IS BIRD ", "v", "HUMAN IS REPTILE"]
    parts = _EMBEDDED_SYM_RE.split(text)

    results: list[tuple[str, str]] = []
    cur_sym = sym
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # Capturing group — this is a relationship symbol
            cur_sym = part
        else:
            chunk = re.sub(r"\s+", " ", part).strip().strip("/").strip()
            if chunk:
                results.append((cur_sym, chunk))

    return results or [(sym, text.strip())]


# Inverse relationship symbols (used to synthesise back-links).
_INVERSE_SYM: dict[str, str] = {
    "<": ">", ">": "<",
    "^": "v", "v": "^",
    "#": "#", ">>": ">>", "⇔": "⇔",
}


def _normalise_for_matching(name: str) -> str:
    """Normalise a theme name for fuzzy resolution of relationship references.

    Covers the most common divergences found in the thesaurus source:
    - Spaces around parentheses and slashes
    - British/American spelling (ORGANISATION vs ORGANIZATION)
    - FORWARDS vs FORWARD
    """
    name = re.sub(r"\s+", " ", name.strip())
    name = re.sub(r"\s*\(\s*", "(", name)
    name = re.sub(r"\s*\)\s*", ")", name)
    name = re.sub(r"\s*/\s*", "/", name)
    name = name.replace("FORWARDS", "FORWARD")
    name = name.replace("ORGANISATION", "ORGANIZATION")
    return name


def _resolve_theme_name(name: str, norm_map: dict[str, str]) -> str:
    """Best-effort resolution of a relationship target name to a real theme name.

    Tries in order:
      1. Exact match via normalised key
      2. name is a strict prefix of one theme name  (e.g. 'HUMAN IS REPTILE'
         → 'HUMAN IS REPTILE/AMPHIBIAN')
      3. One theme name is a strict prefix of name  (e.g. 'CERTAINTY/RELIABILITY
         IS SOLIDITY' → 'CERTAINTY/RELIABILITY IS SOLIDITY/FIRMNESS')

    Returns the canonical theme name, or the original string if unresolved.
    """
    norm = _normalise_for_matching(name)
    if norm in norm_map:
        return norm_map[norm]
    # Prefix: name + "/" starts a theme
    candidates = [v for k, v in norm_map.items() if k.startswith(norm + "/")]
    if len(candidates) == 1:
        return candidates[0]
    # Reverse prefix: a theme is a strict prefix of name
    candidates = [v for k, v in norm_map.items() if norm.startswith(k + "/")]
    if len(candidates) == 1:
        return candidates[0]
    return name   # unresolved — keep original for display


def _extract_domains(theme_name: str) -> tuple[list[str], list[str]]:
    parts = re.split(r"\bIS\b", theme_name, maxsplit=1)
    if len(parts) != 2:
        return [theme_name.strip()], []
    target_str, source_str = parts
    targets = [t.strip() for t in target_str.split("/") if t.strip()]
    sources = [s.strip() for s in source_str.split("/") if s.strip()]
    return targets, sources


def build(thesaurus_path: Path, db_path: Path) -> None:
    data = json.loads(thesaurus_path.read_text())

    db_path.unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)

    # Pre-collect all theme names for fuzzy relationship resolution.
    all_theme_names: set[str] = {
        t["name"]
        for p in data["parts"]
        for t in p["themes"]
    }
    norm_map: dict[str, str] = {
        _normalise_for_matching(n): n for n in all_theme_names
    }

    part_id = theme_id = sub_id = entry_id = wn_id = 0

    for part in data["parts"]:
        part_id += 1
        con.execute("INSERT INTO parts VALUES (?, ?)", (part_id, part["name"]))

        for theme in part["themes"]:
            theme_id += 1
            targets, sources = _extract_domains(theme["name"])
            con.execute(
                "INSERT INTO themes VALUES (?, ?, ?, ?, ?)",
                (theme_id, part_id, theme["name"],
                 json.dumps(targets), json.dumps(sources)),
            )
            for domain in targets:
                con.execute(
                    "INSERT INTO theme_domains VALUES (?, ?, 'target')", (theme_id, domain)
                )
            for domain in sources:
                con.execute(
                    "INSERT INTO theme_domains VALUES (?, ?, 'source')", (theme_id, domain)
                )
            for rel in theme.get("relationships", []):
                for r_sym, r_theme in _resplit_relationship(rel["symbol"], rel["theme"]):
                    resolved = _resolve_theme_name(r_theme, norm_map)
                    con.execute(
                        "INSERT INTO relationships(theme_id, symbol, related_theme)"
                        " VALUES (?, ?, ?)",
                        (theme_id, r_sym, resolved),
                    )

            for pos, sub in enumerate(theme.get("subsections", [])):
                sub_id += 1
                con.execute(
                    "INSERT INTO subsections VALUES (?, ?, ?, ?)",
                    (sub_id, theme_id, sub.get("heading", ""), pos),
                )

                method = ""
                for entry in sub.get("entries", []):
                    entry_id += 1
                    method = entry.get("wn_method", "")
                    hw  = entry.get("headword", "").strip()
                    sl  = entry.get("source_lemma", "")
                    con.execute(
                        "INSERT INTO entries VALUES (?, ?, 'en', ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            entry_id, sub_id, hw, sl,
                            entry.get("reversal_prefix", ""),
                            entry.get("literal_meaning", ""),
                            entry.get("word_class_literal", ""),
                            entry.get("word_class_metaphorical", ""),
                            entry.get("metaphorical_meaning", ""),
                            entry.get("example", ""),
                        ),
                    )
                    con.execute(
                        "INSERT INTO entries_fts(rowid, headword, source_lemma,"
                        " literal_meaning, metaphorical_meaning) VALUES (?, ?, ?, ?, ?)",
                        (
                            entry_id, hw, sl,
                            entry.get("literal_meaning", ""),
                            entry.get("metaphorical_meaning", ""),
                        ),
                    )

                    for role in ("literal", "metaphorical"):
                        match = entry.get(f"wn_{role}")
                        if not match:
                            continue
                        wn_id += 1
                        scores = match.get("scores", {})
                        con.execute(
                            "INSERT INTO wn_matches VALUES (?, ?, ?, ?, 'omw-en:2.0',"
                            " ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                wn_id, entry_id, role, method,
                                match.get("synset_id", ""),
                                match.get("pos", ""),
                                match.get("definition", ""),
                                json.dumps(match.get("lemmas", [])),
                                scores.get("def_score"),
                                scores.get("hyper_score"),
                                scores.get("total"),
                                json.dumps(scores.get("hyper_matched", {})),
                            ),
                        )

    # Add missing inverse relationships so the hierarchy is navigable in
    # both directions (e.g. HUMAN IS ANIMAL →v→ HUMAN IS BIRD gives
    # HUMAN IS BIRD →^→ HUMAN IS ANIMAL automatically).
    inv_added = _add_inverse_relationships(con)

    con.commit()
    con.close()
    print(
        f"Built {db_path}: {part_id} parts, {theme_id} themes,"
        f" {sub_id} subsections, {entry_id} entries, {wn_id} WN matches,"
        f" {inv_added} inferred inverse relationships"
    )


def _add_inverse_relationships(con: "sqlite3.Connection") -> int:
    """Infer and insert missing inverse relationships.

    For every (A, sym, B) in the relationships table where B is a known theme,
    add (B, inv(sym), A) if that row does not already exist.  This ensures the
    hierarchy is navigable in both directions.
    """
    theme_id_map: dict[str, int] = dict(
        con.execute("SELECT name, id FROM themes").fetchall()
    )
    theme_name_map: dict[int, str] = {v: k for k, v in theme_id_map.items()}

    rels = con.execute(
        "SELECT r.theme_id, r.symbol, r.related_theme FROM relationships r"
    ).fetchall()

    added = 0
    for theme_id, sym, related in rels:
        inv_sym = _INVERSE_SYM.get(sym)
        if not inv_sym:
            continue
        target_id = theme_id_map.get(related)
        if not target_id:
            continue   # unresolved reference — skip
        source_name = theme_name_map[theme_id]
        exists = con.execute(
            "SELECT 1 FROM relationships"
            " WHERE theme_id=? AND symbol=? AND related_theme=?",
            (target_id, inv_sym, source_name),
        ).fetchone()
        if not exists:
            con.execute(
                "INSERT INTO relationships(theme_id, symbol, related_theme)"
                " VALUES (?, ?, ?)",
                (target_id, inv_sym, source_name),
            )
            added += 1
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description="Build browser SQLite DB")
    parser.add_argument("--input", default="build/thesaurus_wn.json")
    parser.add_argument("--out", default="web/thesaurus.db")
    args = parser.parse_args()

    inp = Path(args.input)
    if not inp.exists():
        raise SystemExit(f"ERROR: {inp} not found — run build.sh first")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    build(inp, Path(args.out))


if __name__ == "__main__":
    main()
