#!/usr/bin/env bash
set -e
python -m pip install -r requirements.txt
python crawler.py --overwrite --debug --limit-queries 3 --max-pages-per-query 1
