"""dgsi — tools to search, fetch, parse and classify Portuguese court rulings
from the public DGSI database (dgsi.pt) to build a structured
sexual-offence sentencing dataset.

Pipeline: search (build the in-scope pool) -> fetch (cache raw HTML) ->
parse (structured fields) -> classify (rape/minor, filter civil "violação") ->
extract (sentence + suspensão, regex first-pass then LLM).
"""
__all__ = ["http", "parse", "classify", "extract"]

# The six courts in scope, with their Domino database + "por data" view id.
COURTS = {
    "Lisboa":    ("jtrl", "33182fc732316039802565fa00497eec"),
    "Porto":     ("jtrp", "56a6e7121657f91e80257cda00381fdf"),
    "Coimbra":   ("jtrc", "8fe0e606d8f56b22802576c0005637dc"),
    "Guimarães": ("jtrg", "86c25a698e4e7cb7802579ec004d3832"),
    "Évora":     ("jtre", "134973db04f39bf2802579bf005f080b"),
    "STJ":       ("jstj", "954f0ce6ad9dd8b980256b5f003fa814"),
}

YEAR_MIN, YEAR_MAX = 2020, 2026
