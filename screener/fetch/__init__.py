"""Fetch step: one module per source, download only, no parsing.

Phase 1 fetches nothing; the fixture stands in for downloaded files. From phase 2 each
module writes its files to data/raw/<source>/<date>/ untouched, with a manifest.json
recording each file's kind, the URL it came from and when it was downloaded.
"""
