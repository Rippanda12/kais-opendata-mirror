#!/bin/sh
# Pull the oblasts the fetch-oblasts workflow put in the draft release "staging" and rebuild the
# local site (data/site). Raw tars are left in the release unless --raw is given.
set -e
cd "$(dirname "$0")/.."
mkdir -p data/out
gh release download staging --dir data/out --clobber --pattern '*.parquet' --pattern '*-???.pmtiles' --pattern 'manifest-*.json'
if [ "$1" = "--raw" ]; then
  gh release download staging --dir data/out --clobber --pattern 'raw-*.tar'
  for t in data/out/raw-*.tar; do tar -xf "$t" -C data/raw && rm "$t"; done
fi
.venv/bin/python -m kaismirror site
