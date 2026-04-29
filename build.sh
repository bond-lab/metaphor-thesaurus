#!/usr/bin/env bash
# build.sh — run the full metaphor-thesaurus pipeline
#
# Usage:
#   ./build.sh                 full run (embeddings, all entries)
#   ./build.sh --overlap       use Jaccard overlap instead of embeddings
#   ./build.sh --limit 50      smoke-test: first N entries, writes to build/thesaurus_wn_test.json
#
# THE_THESAURUS.docx is downloaded automatically from the John Benjamins website
# if not already present. It is not redistributable and is excluded from git.

set -euo pipefail
cd "$(dirname "$0")"

PYTHON=".venv/bin/python"
SCRIPTS="scripts"
BUILD="build"
THESAURUS_DOCX="THE_THESAURUS.docx"
METHOD="embeddings"
SIMCSE=0
LIMIT=0

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --overlap) METHOD="overlap" ;;
        --simcse)  SIMCSE=1 ;;
        --limit)   LIMIT="$2"; shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
    shift
done

mkdir -p "$BUILD"

# ── 0a. Thesaurus DOCX ────────────────────────────────────────────────────
THESAURUS_URL="https://www.benjamins.com/series/hcp/78/THE_THESAURUS.docx"
echo "=== Step 0a: thesaurus source ==="
if [[ ! -f "$THESAURUS_DOCX" ]]; then
    echo "Downloading $THESAURUS_DOCX ..."
    curl -fL "$THESAURUS_URL" -o "$THESAURUS_DOCX"
else
    echo "  $THESAURUS_DOCX already present"
fi

# ── 0. Python environment ──────────────────────────────────────────────────
echo "=== Step 0: python environment ==="
if ! command -v uv &>/dev/null; then
    echo "ERROR: uv not found. Install from https://docs.astral.sh/uv/" >&2
    exit 1
fi
if [[ ! -d ".venv" ]]; then
    echo "Creating virtual environment ..."
    uv venv --python 3.12
fi
echo "Installing dependencies from requirements.txt ..."
uv pip install -r requirements.txt --quiet
# WordNet data is downloaded to build/wn-data/ by setup_wn() inside the scripts

# When --limit is set this is a smoke-test run: write to a separate file so
# we never overwrite a complete thesaurus_wn.json with a partial result.
if [[ $LIMIT -gt 0 ]]; then
    WN_OUT="$BUILD/thesaurus_wn_test.json"
    RESUME_FLAG=""
    LIMIT_ARG="--limit $LIMIT"
    echo "*** Smoke-test mode (limit=$LIMIT) → output: $WN_OUT ***"
else
    WN_OUT="$BUILD/thesaurus_wn.json"
    RESUME_FLAG="--resume"
    LIMIT_ARG=""
fi

# ── 1. Extract thesaurus ───────────────────────────────────────────────────
echo ""
echo "=== Step 1: extract thesaurus ==="
if [[ ! -f "$THESAURUS_DOCX" ]]; then
    echo "ERROR: $THESAURUS_DOCX not found" >&2
    exit 1
fi
$PYTHON "$SCRIPTS/extract.py"
mv -f thesaurus.json "$BUILD/thesaurus.json"

# ── 2. Run tests ───────────────────────────────────────────────────────────
echo ""
echo "=== Step 2: run tests ==="
$PYTHON tests/test_wordnet_match.py

# ── 3. WordNet matching ────────────────────────────────────────────────────
echo ""
echo "=== Step 3: wordnet matching (method=$METHOD) ==="
$PYTHON "$SCRIPTS/wordnet_match.py" \
    --method    "$METHOD" \
    --thesaurus "$BUILD/thesaurus.json" \
    --cache     "$BUILD/embeddings_cache.json" \
    --out       "$WN_OUT" \
    $RESUME_FLAG \
    $LIMIT_ARG

# ── 4. Domain analysis (full run only) ────────────────────────────────────
if [[ $LIMIT -eq 0 ]]; then
    echo ""
    echo "=== Step 4: domain analysis ==="
    $PYTHON "$SCRIPTS/analyse_domains.py" \
        --input     "$WN_OUT" \
        --min-count 2 \
        > "$BUILD/domain_analysis.txt"
    echo "Domain analysis → $BUILD/domain_analysis.txt"

    echo ""
    echo "=== Step 5: domain maps (source / target / combined) ==="
    for ROLE in source target combined; do
        $PYTHON "$SCRIPTS/build_domain_map.py" \
            --input     "$WN_OUT" \
            --role      "$ROLE" \
            --min-count 2 \
            --out       "$BUILD/${ROLE}_domain_map.toml"
    done

    echo ""
    echo "=== Step 6: compare source vs target domain maps ==="
    $PYTHON "$SCRIPTS/compare_domain_maps.py" \
        --source   "$BUILD/source_domain_map.toml" \
        --target   "$BUILD/target_domain_map.toml" \
        --combined "$BUILD/combined_domain_map.toml" \
        > "$BUILD/domain_map_comparison.txt"
    echo "Comparison → $BUILD/domain_map_comparison.txt"
fi

if [[ $SIMCSE -eq 1 && $LIMIT -eq 0 ]]; then
    SIMCSE_OUT="$BUILD/thesaurus_wn_simcse.json"
    echo ""
    echo "=== SimCSE run ==="
    $PYTHON "$SCRIPTS/wordnet_match.py" \
        --method    simcse \
        --thesaurus "$BUILD/thesaurus.json" \
        --cache     "$BUILD/embeddings_cache.json" \
        --out       "$SIMCSE_OUT" \
        --resume
    echo "SimCSE output → $SIMCSE_OUT"
fi

echo ""
echo "Results written to $BUILD/:"
ls -lh "$BUILD/"
