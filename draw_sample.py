#!/usr/bin/env python3
"""Draw the reproducible pilot sample from the in-scope pool.

A fixed seed keeps the draw reproducible: same pool + same seed => same rows,
so anyone can re-derive the pilot and check the numbers. The draw is a simple
random sample of the whole pool rather than a stratified one — the point of the
pilot is to estimate the suspension rate without baking in an assumption about
how adults and minors differ, and at n=100 out of ~400 an unstratified draw
lands within a couple of points of the population split anyway.

Usage:  python3 draw_sample.py [--pool pool.csv] [--n 100] [--seed 42]
"""
from __future__ import annotations
import argparse
import csv
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="pool.csv")
    ap.add_argument("--out", default="sample_100.csv")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    pool_path = ROOT / args.pool
    if not pool_path.exists():
        print(f"missing {pool_path} — run build_pool.py first", file=sys.stderr)
        return 1

    rows = list(csv.DictReader(pool_path.open(encoding="utf-8")))
    if not rows:
        print(f"{pool_path} is empty", file=sys.stderr)
        return 1
    if args.n > len(rows):
        print(f"asked for {args.n} but pool has {len(rows)}", file=sys.stderr)
        return 1

    random.seed(args.seed)
    draw = random.sample(rows, args.n)

    out = ROOT / args.out
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(draw)

    dc, pc = Counter(r["categoria"] for r in draw), Counter(r["categoria"] for r in rows)
    n, m = len(draw), len(rows)
    print(f"drew {n} of {m} (seed {args.seed}) -> {out}")
    for cat in sorted(pc):
        print(f"  {cat:<7} {dc[cat]:>3} ({dc[cat]/n*100:.0f}%)   "
              f"population {pc[cat]:>3} ({pc[cat]/m*100:.0f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
