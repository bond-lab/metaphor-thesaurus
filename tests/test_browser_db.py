"""Tests for scripts/make_browser_db.py — schema and row-count sanity."""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from make_browser_db import build

THESAURUS = Path(__file__).parent.parent / "build" / "thesaurus_wn.json"
requires_thesaurus = pytest.mark.skipif(
    not THESAURUS.exists(), reason="build/thesaurus_wn.json not found — run build.sh first"
)


@pytest.fixture(scope="module")
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    build(THESAURUS, path)
    yield path
    path.unlink(missing_ok=True)


def q(con, sql, params=()):
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


@requires_thesaurus
class TestSchema:
    def test_tables_exist(self, db_path):
        con = sqlite3.connect(db_path)
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        for name in ("parts", "themes", "relationships", "subsections",
                     "entries", "theme_domains", "wn_matches"):
            assert name in tables, f"Missing table: {name}"
        con.close()

    def test_fts_exists(self, db_path):
        con = sqlite3.connect(db_path)
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "entries_fts" in tables
        con.close()

    def test_entries_have_lang(self, db_path):
        con = sqlite3.connect(db_path)
        cols = {r[1] for r in con.execute("PRAGMA table_info(entries)")}
        assert "lang" in cols
        con.close()

    def test_wn_matches_have_lexicon_and_method(self, db_path):
        con = sqlite3.connect(db_path)
        cols = {r[1] for r in con.execute("PRAGMA table_info(wn_matches)")}
        assert "lexicon" in cols
        assert "method" in cols
        con.close()


@requires_thesaurus
class TestRowCounts:
    def test_parts(self, db_path):
        con = sqlite3.connect(db_path)
        n = con.execute("SELECT COUNT(*) FROM parts").fetchone()[0]
        assert n == 6, f"Expected 6 parts, got {n}"
        con.close()

    def test_themes(self, db_path):
        con = sqlite3.connect(db_path)
        n = con.execute("SELECT COUNT(*) FROM themes").fetchone()[0]
        assert 300 <= n <= 400, f"Expected ~339 themes, got {n}"
        con.close()

    def test_entries(self, db_path):
        con = sqlite3.connect(db_path)
        n = con.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        assert n >= 8000, f"Expected 8000+ entries, got {n}"
        con.close()

    def test_wn_matches(self, db_path):
        con = sqlite3.connect(db_path)
        n = con.execute("SELECT COUNT(*) FROM wn_matches").fetchone()[0]
        # Two rows per matched entry (literal + metaphorical)
        assert n >= 10000, f"Expected 10000+ wn_match rows, got {n}"
        con.close()

    def test_theme_domains(self, db_path):
        con = sqlite3.connect(db_path)
        n_src = con.execute(
            "SELECT COUNT(DISTINCT domain) FROM theme_domains WHERE role='source'"
        ).fetchone()[0]
        n_tgt = con.execute(
            "SELECT COUNT(DISTINCT domain) FROM theme_domains WHERE role='target'"
        ).fetchone()[0]
        assert n_src >= 200, f"Expected 200+ source domains, got {n_src}"
        assert n_tgt >= 200, f"Expected 200+ target domains, got {n_tgt}"
        con.close()


@requires_thesaurus
class TestContent:
    def test_sapphire_entry(self, db_path):
        con = sqlite3.connect(db_path)
        rows = q(con, "SELECT * FROM entries WHERE headword = 'sapphire'")
        assert len(rows) >= 1, "sapphire not found"
        e = rows[0]
        assert "blue" in e["literal_meaning"].lower() or e["literal_meaning"] != ""
        con.close()

    def test_sapphire_wn_matches(self, db_path):
        con = sqlite3.connect(db_path)
        rows = q(con, """
            SELECT wm.role, wm.synset_id
            FROM wn_matches wm
            JOIN entries e ON e.id = wm.entry_id
            WHERE e.headword = 'sapphire'
        """)
        roles = {r["role"] for r in rows}
        assert "literal" in roles
        assert "metaphorical" in roles
        con.close()

    def test_fts_search(self, db_path):
        con = sqlite3.connect(db_path)
        rows = q(con, """
            SELECT e.headword FROM entries_fts fts
            JOIN entries e ON e.id = fts.rowid
            WHERE entries_fts MATCH '"sapphire"*'
        """)
        headwords = {r["headword"] for r in rows}
        assert "sapphire" in headwords
        con.close()

    def test_domain_split(self, db_path):
        con = sqlite3.connect(db_path)
        mineral_src = con.execute(
            "SELECT COUNT(*) FROM theme_domains WHERE domain='MINERAL' AND role='source'"
        ).fetchone()[0]
        colour_tgt = con.execute(
            "SELECT COUNT(*) FROM theme_domains WHERE domain='COLOUR' AND role='target'"
        ).fetchone()[0]
        assert mineral_src >= 1, "MINERAL not found as source domain"
        assert colour_tgt >= 1, "COLOUR not found as target domain"
        con.close()

    def test_wn_matches_json_columns(self, db_path):
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT lemmas, hyper_matched FROM wn_matches LIMIT 1"
        ).fetchone()
        assert row is not None
        lemmas = json.loads(row[0])
        hyper = json.loads(row[1])
        assert isinstance(lemmas, list)
        assert isinstance(hyper, dict)
        con.close()

    def test_theme_domains_json(self, db_path):
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT target_domains, source_domains FROM themes WHERE name LIKE '%IS MINERAL%' LIMIT 1"
        ).fetchone()
        assert row is not None
        targets = json.loads(row[0])
        sources = json.loads(row[1])
        assert "MINERAL" in sources
        con.close()
