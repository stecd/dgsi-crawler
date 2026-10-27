#!/usr/bin/env python3
"""Write each in-scope pilot ruling to its own text file, for reading in full.

The extraction pass reads one ruling per file rather than re-deriving it from the
HTML cache each time. Splitting it out has one hard requirement: the whole ruling
goes in the file. The decisão — where suspensão is actually pronounced — sits at
the END of a ruling, so any head-cap silently drops the evidence. Measured on the
pilot: an earlier 24,000-char cap discarded 73% of the text and hid the only
suspensão mention in 12 of 96 rulings, which would have biased the rate down while
looking authoritative. Rulings run to ~357k chars against a 1M-token window, so
they fit whole. There is no cap here; the guard raises instead.

Rulings are re-classified on the way through (the refined criminal/civil test that
needs Meio Processual), so the civil-violação false positives never reach the
extraction pass at all.

Output is derived and gitignored — regenerate with this script.

Usage:  python3 dump_rulings.py [--sample sample_100.csv] [--out data/rulings]
"""
from __future__ import annotations
import argparse
import csv
import json
import sys
from pathlib import Path

from dgsi import http, parse, classify, extract

ROOT = Path(__file__).resolve().parent


def slug(processo: str) -> str:
    return processo.replace("/", "_").replace(".", "-")


def render(row: dict, fields: dict, cls: dict, text: str) -> str:
    return (
        f"PROCESSO: {row['num_processo']}\n"
        f"TRIBUNAL: {row['court']}\n"
        f"DATA: {fields.get('data','')}\n"
        f"RELATOR: {fields.get('relator','')}\n"
        f"MEIO PROCESSUAL: {fields.get('meio_processual','')}\n"
        f"DESCRITORES: {' | '.join(fields['descritores'])}\n"
        f"CATEGORIA: {cls['categoria']}\n\n"
        f"=== SUMÁRIO ===\n{fields.get('sumario','')}\n\n"
        f"=== TEXTO INTEGRAL ===\n{text}\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default="sample_100.csv")
    ap.add_argument("--out", default="data/rulings")
    args = ap.parse_args()

    sample = ROOT / args.sample
    if not sample.exists():
        print(f"missing {sample}", file=sys.stderr)
        return 1

    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)

    rows = list(csv.DictReader(sample.open(encoding="utf-8")))
    if not rows:
        print(f"{sample} is empty", file=sys.stderr)
        return 1

    manifest, rejected, errors = [], [], 0
    for i, r in enumerate(rows, 1):
        try:
            html = http.get(r["url"], encoding="latin-1")
            fields = parse.parse_ruling(html)
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"[{i:>3}/{len(rows)}] ERROR {r['num_processo']}: {e}", file=sys.stderr)
            continue

        cls = classify.classify(fields["descritores"],
                               fields.get("meio_processual", ""),
                               fields.get("seccao", ""))
        if not cls["is_scope"]:
            rejected.append((r["num_processo"], cls["reason"]))
            print(f"[{i:>3}/{len(rows)}] skip {r['num_processo']:<24} {cls['reason']}")
            continue

        text = fields.get("texto_integral") or fields.get("sumario") or ""
        if len(text) > extract.MAX_TEXT_CHARS:
            # Never truncate — see module docstring.
            raise RuntimeError(
                f"{r['num_processo']}: {len(text):,} chars exceeds the "
                f"{extract.MAX_TEXT_CHARS:,} guard. Do not add a cap; split the "
                "ruling or raise the guard deliberately.")

        path = out / f"{slug(r['num_processo'])}.txt"
        path.write_text(render(r, fields, cls, text), encoding="utf-8")
        manifest.append({
            "num_processo": r["num_processo"], "court": r["court"],
            "categoria": cls["categoria"],
            "file": str(path.relative_to(ROOT)), "chars": len(text),
        })

    (ROOT / "data" / "rulings_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    total = sum(m["chars"] for m in manifest)
    print(f"\nin-scope rulings written:   {len(manifest)}")
    print(f"false positives excluded:   {len(rejected)}")
    for p, why in rejected:
        print(f"  - {p}: {why}")
    print(f"fetch errors:               {errors}")
    print(f"total chars to read:        {total:,}  (~{total/3.5/1000:.0f}k tokens)")
    print(f"\nwrote {out}/ and data/rulings_manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
