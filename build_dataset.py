#!/usr/bin/env python3
"""Build the structured dataset from the pilot sample.

Reads sample_100.csv (the seeded random 100), fetches each ruling (cached),
parses its fields, re-classifies with the refined criminal/civil logic, runs the
regex first-pass for suspensão, and — if ANTHROPIC_API_KEY is set — the accurate
LLM extraction. Writes:

  data/out/dataset.csv   one row per ruling, ready to import to Google Sheets
  data/out/quality_report.txt   coverage, false-positive flags, suspensão first-pass

Usage:  python3 build_dataset.py [--limit N] [--llm]
"""
from __future__ import annotations
import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

from dgsi import http, parse, classify, extract

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "data" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

COLUMNS = [
    "num_processo", "tribunal", "ano", "data", "categoria_original", "categoria_refinada",
    "in_scope", "reject_reason", "meio_processual", "seccao", "relator",
    "suspensao_execucao", "suspensao_conf", "suspensao_periodo_anos", "penas_detectadas",
    "descritores", "url",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default="sample_100.csv",
                    help="crawl target CSV (e.g. sample_balanced.csv)")
    ap.add_argument("--out", default="dataset.csv", help="output CSV name in data/out/")
    ap.add_argument("--limit", type=int, default=0, help="process only first N (0 = all)")
    ap.add_argument("--llm", action="store_true", help="also run LLM extraction (needs API key)")
    args = ap.parse_args()

    sample_path = ROOT / args.sample
    if not sample_path.exists():
        print(f"missing {sample_path}", file=sys.stderr)
        return 1

    rows = list(csv.DictReader(sample_path.open(encoding="utf-8")))
    if args.limit:
        rows = rows[: args.limit]

    use_llm = args.llm and extract.llm_available()
    if args.llm and not use_llm:
        print("!! --llm requested but ANTHROPIC_API_KEY not set; running regex pass only\n")

    out_rows = []
    fp = []                       # false positives (were counted in-scope, now rejected)
    susp = Counter()
    errors = 0

    for i, r in enumerate(rows, 1):
        url = r["url"]
        try:
            html = http.get(url, encoding="latin-1")
            fields = parse.parse_ruling(html)
        except Exception as e:      # noqa: BLE001
            errors += 1
            print(f"[{i:>3}/{len(rows)}] ERROR {r['num_processo']}: {e}", file=sys.stderr)
            continue

        cls = classify.classify(fields["descritores"],
                                fields.get("meio_processual", ""),
                                fields.get("seccao", ""))
        rx = extract.regex_pass(fields.get("sumario", ""), fields.get("texto_integral", ""))

        tribunal = r.get("court") or r.get("tribunal", "")
        refined = cls["categoria"] or "REJECT"
        if not cls["is_scope"]:
            fp.append((r["num_processo"], tribunal, cls["reason"]))
        else:
            susp[rx["suspensao_execucao"]] += 1

        row = {
            # The crawl-target id (from the pool/sample) is canonical: the manifest,
            # the dumped ruling files, and the extraction records all key off it.
            # DGSI occasionally spells the processo differently in the ruling body
            # than in the search index (e.g. FLRS vs PFLRS) — preferring the parsed
            # value there silently breaks the join, so keep the sample's id.
            "num_processo": r["num_processo"],
            "tribunal": tribunal,
            "ano": fields.get("ano") or r.get("ano", ""),
            "data": fields.get("data", ""),
            "categoria_original": r.get("categoria", ""),
            "categoria_refinada": refined,
            "in_scope": "sim" if cls["is_scope"] else "não",
            "reject_reason": "" if cls["is_scope"] else cls["reason"],
            "meio_processual": fields.get("meio_processual", ""),
            "seccao": fields.get("seccao", ""),
            "relator": fields.get("relator", ""),
            "descritores": " | ".join(fields["descritores"]),
            "url": url,
            **rx,
        }

        if use_llm and cls["is_scope"]:
            try:
                row.update({f"llm_{k}": v for k, v in extract.llm_extract(fields).items()})
            except Exception as e:  # noqa: BLE001
                print(f"    llm skip {r['num_processo']}: {e}", file=sys.stderr)

        out_rows.append(row)
        flag = "" if cls["is_scope"] else "  <-- FALSE POSITIVE: " + cls["reason"]
        print(f"[{i:>3}/{len(rows)}] {row['num_processo']:<22} {refined:<7} "
              f"susp={rx['suspensao_execucao']:<18}{flag}")

    # write dataset
    all_cols = COLUMNS + sorted({k for row in out_rows for k in row if k.startswith("llm_")})
    with (OUT_DIR / args.out).open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=all_cols)
        w.writeheader()
        for row in out_rows:
            w.writerow({c: row.get(c, "") for c in all_cols})

    # quality report
    in_scope = sum(1 for row in out_rows if row["in_scope"] == "sim")
    lines = [
        "DATA QUALITY REPORT",
        "=" * 40,
        f"rulings processed:        {len(out_rows)}",
        f"fetch errors:             {errors}",
        f"confirmed in-scope:       {in_scope}",
        f"false positives removed:  {len(fp)}   (were counted, but civil 'violação' / out of scope)",
        "",
        "Suspensão de execução (regex first-pass, in-scope only):",
        f"  suspended (sim):        {susp['sim']}",
        f"  not/indeterminate:      {susp['não/indeterminado']}",
        f"  -> suspension rate:     {susp['sim']/max(1,in_scope)*100:.0f}%  (FIRST-PASS — needs LLM/manual confirm)",
        "",
        "False positives (excluded on refined classification):",
    ]
    for p, c, reason in fp:
        lines.append(f"  - {p} ({c}): {reason}")
    if not fp:
        lines.append("  (none)")
    lines += [
        "",
        "NOTES:",
        "  * Suspensão here is a regex first-pass; the accurate figure needs the LLM pass",
        "    (run with --llm once ANTHROPIC_API_KEY is set) or manual review.",
        "  * False positives here are bare-violação civil cases, which only a fetch can",
        "    catch. Prune them from pool.csv and re-draw if the count matters.",
    ]
    (OUT_DIR / "quality_report.txt").write_text("\n".join(lines), encoding="utf-8")

    print("\n" + "\n".join(lines[:12]))
    print(f"\nwrote {OUT_DIR/args.out} and quality_report.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
