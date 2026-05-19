#!/usr/bin/env bash
# Serve the browser locally.  Must be served over HTTP (not file://) for
# sql.js fetch() and DecompressionStream to work.
cd "$(dirname "$0")"
echo "Serving at http://localhost:8080/"
python3 -m http.server 8080
