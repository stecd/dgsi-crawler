#!/usr/bin/env python3
"""Merge an in-session extraction pass into the pilot dataset — and verify it first.

The extraction pass runs as subagents (see README, "The accurate extraction pass runs
in-session"), each returning one LLM_SCHEMA object per ruling. This script collects
those objects from the workflow journal, checks them, and only then writes the dataset.

Three checks, in order of how much they buy:

1. `fonte_trecho` must appear VERBATIM in the source ruling. This is the only field
   that can be verified mechanically, and it is what licenses trusting the fields that
   can't be. A quote that doesn't grep back is a fabrication or a cross-contamination
   between rulings, and either way the row is quarantined rather than merged. (Both
   have happened: one trial agent caught itself reading another ruling's text.)

2. Art. 50.º CP coherence: suspension is legally capped at 5 years' prison. A row
   saying pena_final > 5 anos AND suspensao_execucao = "sim" is self-contradictory —
   quarantine it. Ditto suspensao_legalmente_possivel="não" with suspensao="sim".

3. Rate arithmetic: only tipo_decisao="condenação" enters the denominator. DGSI
   publishes sexual-offence rulings that are not convictions at all (appeals about
   prisão preventiva, about an acusação not being received). They have no pena, so
   there is no execução to suspend; counting them as "not suspended" would drag the
   headline rate down while looking rigorous.

Usage:
  python3 merge_extraction.py --journal <path/to/journal.jsonl> [--out data/out]
"""
from __future__ import annotations
import argparse
import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Fields the extraction pass produces, in the order they should appear in the CSV.
LLM_FIELDS = [
    "tipo_decisao", "recorrente", "condenacao_1a_inst", "decisao_recurso", "pena_final",
    "suspensao_execucao", "suspensao_periodo_anos", "suspensao_condicoes",
    "suspensao_legalmente_possivel", "antecedentes_criminais",
    "vitima_idade", "vitima_relacao", "vitimas_multiplas", "pena_acessoria",
    "fundamentacao_facto", "fundamentacao_direito",
    "fonte_trecho", "fonte_offset_aprox", "notas_extracao",
]


def norm(s: str) -> str:
    """Normalise for verbatim comparison without letting real differences through.

    Only collapses things that are typographic noise: unicode form, non-breaking
    spaces, smart quotes, runs of whitespace. Deliberately does NOT lowercase or
    strip accents — those carry meaning in Portuguese, and a match that needs them
    ignored is not a verbatim match.
    """
    s = unicodedata.normalize("NFC", s)
    s = (s.replace(" ", " ").replace("’", "'")
          .replace("“", '"').replace("”", '"')
          .replace("–", "-").replace("—", "-"))
    return re.sub(r"\s+", " ", s).strip()


def key(processo: str) -> str:
    """Match key for a processo number.

    DGSI processo numbers carry stray spaces ("2141/17.6T9PTM. E1", "872/20.2 JAAVR.P1")
    and an extractor reading the ruling reasonably writes them back without. That is a
    formatting difference, not a different case, so join on a space-stripped key rather
    than dropping the row.
    """
    return re.sub(r"\s+", "", processo or "").upper()


_ANOS = re.compile(r"(\d{1,2})\s*anos?(?:\s+e\s+(\d{1,2})\s*(?:meses|m[êe]s))?", re.I)

# Art. 50.º suspends prison, and only prison. A conviction to a fine (multa), or a
# medida de segurança applied to an inimputável (art. 98.º — which the rulings ALSO
# describe with "execução se suspende"), has no prison term to suspend. Those are
# genuinely "não aplicável" and belong outside the rate, not inside it as "não".
_PRISAO_NA_PENA = re.compile(r"pris[ãa]o", re.I)


def pena_anos(pena: str) -> float | None:
    """Years of prison in a pena string, or None if not stated numerically.

    First match only: pena_final is written decision-first ("5 anos e 6 meses de
    prisão (pena única...)"), so the leading figure is the operative one.
    """
    m = _ANOS.search(pena or "")
    if not m:
        return None
    return int(m.group(1)) + (int(m.group(2)) / 12 if m.group(2) else 0)


def load_journal(path: Path) -> list[dict]:
    """Pull the returned LLM_SCHEMA objects out of a workflow journal."""
    recs, seen = [], set()
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") != "result":
            continue
        v = d.get("result", d.get("value"))
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                continue
        if not isinstance(v, dict) or not v.get("num_processo"):
            continue
        # A resumed run can replay a cached agent; keep the last write per processo.
        p = v["num_processo"]
        if p in seen:
            recs = [r for r in recs if r["num_processo"] != p]
        seen.add(p)
        recs.append(v)
    return recs


def verify(recs: list[dict], manifest: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """Split records into (clean, quarantined). Quarantined rows carry `_falha`."""
    clean, bad = [], []
    bodies: dict[str, str] = {}
    by_key = {key(p): f for p, f in manifest.items()}
    for r in recs:
        p = r["num_processo"]
        falhas = []

        f = by_key.get(key(p))
        if not f:
            falhas.append("processo não está no manifest")
        else:
            if f not in bodies:
                bodies[f] = norm((ROOT / f).read_text(encoding="utf-8"))
            trecho = norm(r.get("fonte_trecho", ""))
            if not trecho:
                falhas.append("fonte_trecho vazio")
            elif trecho not in bodies[f]:
                falhas.append("fonte_trecho NÃO consta do acórdão (verbatim)")

        susp = r.get("suspensao_execucao", "")
        legal = r.get("suspensao_legalmente_possivel", "")
        anos = pena_anos(r.get("pena_final", ""))

        if anos is not None and anos > 5.001:
            if susp == "sim":
                falhas.append(f"pena {anos:.2f} anos > 5 mas suspensao=sim (art. 50.º impede)")
            if legal == "sim":
                falhas.append(f"pena {anos:.2f} anos > 5 mas legalmente_possivel=sim")
        if legal == "não" and susp == "sim":
            falhas.append("legalmente_possivel=não mas suspensao=sim (contradição)")
        if (r.get("tipo_decisao") == "condenação" and susp == "não aplicável"
                and _PRISAO_NA_PENA.search(r.get("pena_final", ""))):
            # Only a contradiction when there IS a prison sentence. A conviction to
            # a fine alone has nothing to suspend and correctly reads "não aplicável".
            falhas.append("condenação com pena de prisão mas suspensao=não aplicável")
        if r.get("tipo_decisao") == "interlocutória" and susp in ("sim", "não"):
            falhas.append(f"tipo_decisao=interlocutória mas suspensao={susp} (não há pena)")

        if falhas:
            bad.append({**r, "_falha": " · ".join(falhas)})
        else:
            clean.append(r)
    return clean, bad


def report(clean: list[dict], bad: list[dict], base: dict[str, dict]) -> str:
    """The quality report. Every rate here is over condenações only."""
    L = []
    W = L.append
    total = len(clean) + len(bad)
    W("EXTRACTION QUALITY REPORT")
    W("=" * 66)
    W(f"registos recebidos:        {total}")
    W(f"verificados (limpos):      {len(clean)}")
    W(f"em quarentena:             {len(bad)}")
    if bad:
        W("")
        W("QUARENTENA — não entram no dataset:")
        for r in bad:
            W(f"  - {r['num_processo']:<24} {r['_falha']}")

    by_tipo: dict[str, int] = {}
    for r in clean:
        by_tipo[r.get("tipo_decisao", "?")] = by_tipo.get(r.get("tipo_decisao", "?"), 0) + 1
    W("")
    W("TIPO DE DECISÃO")
    for k, v in sorted(by_tipo.items(), key=lambda x: -x[1]):
        W(f"  {k:<20} {v:>3}")

    cond = [r for r in clean if r.get("tipo_decisao") == "condenação"]
    cat_of = {}
    for r in cond:
        cat_of[r["num_processo"]] = base.get(key(r["num_processo"]), {}).get("categoria_refinada", "")
    # Adults + Minors must reconstitute the whole; a row that joins to neither is a
    # broken join, not a third category, and silently shrinks a published subtotal.
    orfaos = [r for r in cond if cat_of[r["num_processo"]] not in ("Adults", "Minors")]

    def has_cond(r):
        c = (r.get("suspensao_condicoes") or "").strip()
        return bool(c) and not c.lower().startswith("nenhuma")

    def group_block(rows, title):
        """Everything about one group, on its OWN denominator. The brief asked for
        the groups reported separately and never pooled — this is that block."""
        multa = [r for r in rows if r.get("suspensao_execucao") == "não aplicável"]
        sim = [r for r in rows if r.get("suspensao_execucao") == "sim"]
        nao = [r for r in rows if r.get("suspensao_execucao") == "não"]
        den = len(sim) + len(nao)
        podia = [r for r in rows if r.get("suspensao_legalmente_possivel") == "sim"]
        podia_sim = [r for r in podia if r.get("suspensao_execucao") == "sim"]
        nao_podia = [r for r in rows if r.get("suspensao_legalmente_possivel") == "não"]
        W("")
        W(f"── {title}  (n={len(rows)} condenações)".ljust(66, "─"))
        if multa:
            W(f"   {len(multa)} só a multa — sem prisão a suspender, fora do denominador")
        W(f"   suspensa / condenações a prisão : {len(sim):>3} / {den:<3} = "
          f"{(str(round(100*len(sim)/den))+'%') if den else 'n/d':>4}")
        W(f"   art. 50.º proibia (pena > 5 anos): {len(nao_podia):>3}   "
          f"({round(100*len(nao_podia)/den) if den else 0}% das condenações)")
        W(f"   onde a suspensão era possível    : {len(podia_sim):>3} / {len(podia):<3} = "
          f"{(str(round(100*len(podia_sim)/len(podia)))+'%') if podia else 'n/d':>4}"
          f"   <- a pergunta de investigação")
        if sim:
            wc = [r for r in sim if has_cond(r)]
            W(f"   suspensões com condições impostas: {len(wc):>3} / {len(sim):<3} = "
              f"{round(100*len(wc)/len(sim))}%   (testa 'sem acompanhamento')")
        return len(sim), den, len(podia_sim), len(podia)

    W("")
    W("TAXA DE SUSPENSÃO — POR GRUPO, cada um no seu próprio denominador")
    W("=" * 66)
    W("O pedido foi: grupos separados e NÃO agregados. Cada bloco abaixo")
    W("responde-se a si próprio; a linha 'onde a suspensão era possível' é a que")
    W("mais se aproxima da pergunta dela.")
    adults = [r for r in cond if cat_of[r["num_processo"]] == "Adults"]
    minors = [r for r in cond if cat_of[r["num_processo"]] == "Minors"]
    group_block(adults, "ADULTOS")
    group_block(minors, "MENORES")

    if orfaos:
        W("")
        W(f"  !! {len(orfaos)} condenação(ões) sem categoria — join partido, corrigir:")
        for r in orfaos:
            W(f"     {r['num_processo']}")

    # The pooled number is reported ONCE, explicitly flagged as not-for-quoting-alone,
    # so nobody reconstructs it from the group blocks and treats it as the headline.
    sim_all = sum(1 for r in cond if r.get("suspensao_execucao") == "sim")
    den_all = sum(1 for r in cond if r.get("suspensao_execucao") in ("sim", "não"))
    W("")
    W("(referência, NÃO citar isolado) taxa agregada adultos+menores:")
    W(f"   {sim_all} / {den_all} = {round(100*sim_all/den_all) if den_all else 0}% — "
      "mistura dois grupos com dinâmicas diferentes. Ver os blocos acima.")

    W("")
    W("COBERTURA POR CAMPO (não-indeterminado, sobre os limpos)")
    W("-" * 66)
    for f in LLM_FIELDS:
        n = sum(1 for r in clean
                if (r.get(f) or "").strip() not in ("", "indeterminado", "não aplicável"))
        W(f"  {f:<32} {n:>3}/{len(clean)}  {100*n/len(clean) if clean else 0:>3.0f}%")

    W("")
    W("CAVEAT (repetir em qualquer publicação): o DGSI só publica acórdãos de 2.ª")
    W("instância e do STJ — cerca de 10% dos casos, enviesados para os graves e os")
    W("recorridos. Esta taxa é a taxa NOS ACÓRDÃOS PUBLICADOS, não nas condenações")
    W("em geral, e as duas não são intermutáveis.")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", required=True, nargs="+",
                    help="one or more workflow journal.jsonl files. When a ruling appears "
                         "in more than one (e.g. a re-read), the LATER file wins.")
    ap.add_argument("--out", default="data/out")
    ap.add_argument("--base", default="data/out/dataset.csv",
                    help="metadata source — use the dataset built from the SAME sample "
                         "the journals cover (dataset_balanced.csv for the balanced set)")
    args = ap.parse_args()

    # Load and merge every journal; later files override earlier per processo, so a
    # top-up run's records replace any stale copy from an earlier pass.
    by_proc: dict[str, dict] = {}
    order: list[str] = []
    for jp in args.journal:
        p = Path(jp)
        if not p.exists():
            print(f"missing {p}", file=sys.stderr)
            return 1
        for r in load_journal(p):
            k = key(r["num_processo"])
            if k not in by_proc:
                order.append(k)
            by_proc[k] = r
    recs = [by_proc[k] for k in order]
    if not recs:
        print(f"no LLM_SCHEMA records in {args.journal} — nothing to merge", file=sys.stderr)
        return 1

    manifest = {m["num_processo"]: m["file"]
                for m in json.loads((ROOT / "data" / "rulings_manifest.json").read_text())}
    base = {key(r["num_processo"]): r
            for r in csv.DictReader((ROOT / args.base).open(encoding="utf-8"))}

    clean, bad = verify(recs, manifest)

    got = {key(r["num_processo"]) for r in recs}
    missing = sorted(p for p in manifest if key(p) not in got)
    if missing:
        print(f"WARNING: {len(missing)} rulings had no extraction record:", file=sys.stderr)
        for p in missing:
            print(f"  - {p}", file=sys.stderr)

    outdir = ROOT / args.out
    outdir.mkdir(parents=True, exist_ok=True)

    # Dataset = descriptor/court metadata from the crawl + verified extraction.
    meta_cols = ["num_processo", "tribunal", "ano", "data", "categoria_refinada",
                 "meio_processual", "seccao", "relator", "descritores", "url"]
    rx_cols = ["suspensao_execucao_regex", "suspensao_conf_regex"]
    cols = meta_cols + LLM_FIELDS + rx_cols

    path = outdir / "dataset_llm.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in sorted(clean, key=lambda x: x["num_processo"]):
            b = base.get(key(r["num_processo"]), {})
            row = {c: b.get(c, "") for c in meta_cols}
            # The crawl's processo spelling is canonical (it came from DGSI), but fall
            # back to the extractor's if the join missed, so no row is ever keyless.
            row["num_processo"] = b.get("num_processo") or r["num_processo"]
            row.update({f: r.get(f, "") for f in LLM_FIELDS})
            # Keep the regex verdict alongside, so the disagreement stays auditable.
            row["suspensao_execucao_regex"] = b.get("suspensao_execucao", "")
            row["suspensao_conf_regex"] = b.get("suspensao_conf", "")
            w.writerow(row)

    if bad:
        qpath = outdir / "quarantine.csv"
        with qpath.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["num_processo", "_falha"] + LLM_FIELDS,
                               extrasaction="ignore")
            w.writeheader()
            for r in bad:
                w.writerow(r)
        print(f"quarantined {len(bad)} -> {qpath}")

    rep = report(clean, bad, base)
    (outdir / "quality_report_llm.txt").write_text(rep + "\n", encoding="utf-8")
    print(rep)
    print(f"\nwrote {path} ({len(clean)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
