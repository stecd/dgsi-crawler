"""Search a DGSI court database by descriptor and parse the results table.

The Domino SearchView endpoint answers a query like

    /{db}.nsf/{viewID}?SearchView&Query=[Descritores] CONTAINS "violação"&SearchMax=0

with an HTML results table whose rows already carry SESSÃO (date), PROCESSO,
RELATOR and DESCRITORES. That is everything the descriptor-only classification
needs, so building the pool costs one request per (court, term) rather than one
per ruling.

Three traps this module handles, all discovered against the live server:

* `[Descritores] CONTAINS` is not a hard field filter — it also matches the
  query in the body text. Rows come back with `descritores` parsed from the
  table so callers can re-check the hit against the real descriptors and drop
  the body-text leakage.
* Domino's search folds accents but not spelling. "coação sexual" (post-1990
  orthography) and "coacção sexual" (pre-1990) are different queries returning
  different rulings, so a term needs every spelling it is filed under.
* The server caps a result *page* at 1000 rows and reports that cap as the
  total ("1000 documents found"), silently truncating. Broad terms exceed it:
  violação in STJ is really 1026. `&Start=` pages past the cap and the pages
  are disjoint, so every search here is paged to exhaustion. Date-restricted
  queries would be the better partition, but DGSI indexes no queryable date
  field — [Data] and its variants all return 0 or a parse error — so Start is
  the only lever available.
"""
from __future__ import annotations
import re
import urllib.parse

from bs4 import BeautifulSoup

from . import http, COURTS

_COUNT_RE = re.compile(r"<h4>([\d,]+)\s+document", re.I)
_PAGE = 1000          # server-side cap on rows per response
_MAX_PAGES = 20       # backstop against a paging loop that never drains


def search_url(db: str, view: str, term: str, start: int = 1) -> str:
    query = urllib.parse.quote(f'[Descritores] CONTAINS "{term}"')
    return (f"https://www.dgsi.pt/{db}.nsf/{view}?SearchView"
            f"&Query={query}&SearchMax=0&Start={start}")


def _cell_descritores(td) -> list[str]:
    parts = re.split(r"<br\s*/?>", td.decode_contents(), flags=re.I)
    vals = [re.sub(r"<[^>]+>", " ", p) for p in parts]
    vals = [re.sub(r"\s+", " ", v).strip().upper() for v in vals]
    return [v for v in vals if v]


def _parse_page(html: str, court: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        # results rows are: icon | sessão | processo (link) | relator | descritores
        if len(tds) < 5:
            continue
        link = tds[2].find("a")
        if not link or not link.get("href"):
            continue
        data = re.sub(r"\s+", " ", tds[1].get_text(" ", strip=True))
        ym = re.search(r"(\d{4})", data)
        rows.append({
            "num_processo": re.sub(r"\s+", " ", link.get_text(strip=True)),
            "court": court,
            "data": data,
            "ano": int(ym.group(1)) if ym else None,
            "relator": re.sub(r"\s+", " ", tds[3].get_text(" ", strip=True)),
            "descritores": _cell_descritores(tds[4]),
            "url": urllib.parse.urljoin("https://www.dgsi.pt/", link["href"]),
        })
    return rows


def search(court: str, term: str) -> list[dict]:
    """Every hit for `term` in `court`, paged past the 1000-row cap.

    One dict per hit: num_processo, court, data, ano, relator, descritores
    (list), url. Empty list when the term matches nothing.
    """
    db, view = COURTS[court]
    out: list[dict] = []
    seen: set[str] = set()

    for page in range(_MAX_PAGES):
        html = http.get(search_url(db, view, term, start=page * _PAGE + 1),
                        encoding="utf-8")
        m = _COUNT_RE.search(html)
        if not m or m.group(1).replace(",", "") == "0":
            break

        rows = _parse_page(html, court)
        for r in rows:
            if r["url"] not in seen:
                seen.add(r["url"])
                out.append(r)
        if len(rows) < _PAGE:       # short page => last page
            break
    return out


def count(court: str, term: str) -> int:
    """Raw hit count including body-text leakage. Pages past the cap, so this
    is the true total rather than the server's truncated `1000`."""
    return len(search(court, term))
