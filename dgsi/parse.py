"""Parse a DGSI ruling page into structured fields.

Ruling documents are a table of label/value rows (Processo, Relator, Descritores,
Data do Acordão, Meio Processual, Secção, Sumário, Decisão Texto Integral, ...).
Pages are latin-1 encoded. Descritores are <br>-delimited tokens.
"""
from __future__ import annotations
import re
from bs4 import BeautifulSoup

# canonical label -> field name we keep
_LABELS = {
    "Processo": "num_processo",
    "Relator": "relator",
    "Descritores": "descritores",
    "Data do Acordão": "data",
    "Data do Acórdão": "data",
    "Meio Processual": "meio_processual",
    "Nº Convencional": "seccao",
    "Secção": "seccao",
    "Votação": "votacao",
    "Decisão": "decisao",
    "Sumário": "sumario",
    "Decisão Texto Integral": "texto_integral",
    "Área Temática": "area_tematica",
}


def _txt(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def parse_ruling(html: str) -> dict:
    """Return a dict of the ruling's fields. Descritores is a list."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict = {"descritores": [], "sumario": "", "texto_integral": ""}

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td", recursive=False) or tr.find_all("td")
        if len(tds) < 2:
            continue
        label = _txt(tds[0]).rstrip(":").strip()
        field = _LABELS.get(label)
        if not field:
            continue
        if field == "descritores":
            # split on <br>; keep uppercase tokens
            parts = re.split(r"<br\s*/?>", tds[1].decode_contents(), flags=re.I)
            vals = [re.sub(r"<[^>]+>", " ", p) for p in parts]
            vals = [re.sub(r"\s+", " ", v).strip().upper() for v in vals]
            out["descritores"] = [v for v in vals if v]
        else:
            out[field] = _txt(tds[1])

    # year from date (MM/DD/YYYY)
    m = re.search(r"(\d{4})", out.get("data", ""))
    out["ano"] = int(m.group(1)) if m else None
    return out
