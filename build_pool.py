#!/usr/bin/env python3
"""Measure the in-scope universe on DGSI and write the pool CSV.

One descriptor search per (court, term) — the results table already carries the
descriptors, so the first-pass classification costs no extra fetches. Rows are
deduped by (court, processo) because a ruling tagged with two in-scope offences
is returned by both searches.

What this pass can and cannot decide: descriptors alone settle the minors side
and every unambiguous adult offence. They cannot settle a bare VIOLAÇÃO, which
also means a non-sexual *breach*; those need Meio Processual / Secção from the
ruling itself, which is build_dataset.py's job. The adult count here is
therefore an upper bound on the violação component.

Usage:  python3 build_pool.py [--out pool.csv]
"""
from __future__ import annotations
import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

from dgsi import COURTS, YEAR_MIN, YEAR_MAX, classify, search

ROOT = Path(__file__).resolve().parent

COLUMNS = ["num_processo", "court", "categoria", "ano", "data", "url", "descritores"]


def collect(terms: list[str]) -> dict[tuple[str, str], dict]:
    """Run every (court, term) search; return deduped in-scope rows."""
    pool: dict[tuple[str, str], dict] = {}
    for court in COURTS:
        for term in terms:
            try:
                hits = search.search(court, term)
            except Exception as e:  # noqa: BLE001
                print(f"  !! {court} / {term}: {e}", file=sys.stderr)
                continue

            kept = 0
            for h in hits:
                # [Descritores] CONTAINS leaks into body text — re-check the hit
                # against the descriptors the results table actually shows.
                cls = classify.classify(h["descritores"])
                if not cls["is_scope"]:
                    continue
                if h["ano"] is None or not (YEAR_MIN <= h["ano"] <= YEAR_MAX):
                    continue
                key = (court, h["num_processo"])
                if key not in pool:
                    pool[key] = {**h, "categoria": cls["categoria"]}
                    kept += 1
            print(f"  {court:<10} {term:<48} {len(hits):>4} hits -> {kept:>3} new in-scope")
    return pool


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="pool.csv")
    args = ap.parse_args()

    terms = classify.SEARCH_TERMS
    print(f"searching {len(COURTS)} courts x {len(terms)} terms, {YEAR_MIN}-{YEAR_MAX}\n")
    pool = collect(terms)

    rows = sorted(pool.values(), key=lambda r: (r["court"], r["ano"], r["num_processo"]))
    out = ROOT / args.out
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({
                "num_processo": r["num_processo"], "court": r["court"],
                "categoria": r["categoria"], "ano": r["ano"], "data": r["data"],
                "url": r["url"], "descritores": " | ".join(r["descritores"]),
            })

    by_cat = Counter(r["categoria"] for r in rows)
    by_court = Counter(r["court"] for r in rows)
    print(f"\n{len(rows)} in-scope  ({by_cat['Adults']} adults / {by_cat['Minors']} minors)")
    for court, n in by_court.most_common():
        c = Counter(r["categoria"] for r in rows if r["court"] == court)
        print(f"  {court:<10} {n:>3}   ({c['Adults']} adults / {c['Minors']} minors)")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
