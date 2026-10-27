"""Classify a ruling as in-scope Adults / Minors (child sexual offence), or reject it.

Scope comes from the research brief, which lists the relevant descriptor
families for each side. The adult side is NOT rape-only: it is
coação sexual, violação, importunação sexual, abuso sexual de pessoa internada,
lenocínio, and abuso sexual de pessoa incapaz de resistência.

Two layers of precision:

1. Descriptor-only (available from search results, no fetch needed) — good enough
   for minors (child descriptors are unambiguous) and for a first-pass count.

2. Refined (needs the fetched ruling's Meio Processual / Secção) — removes the
   civil-"violação" false positives that plague STJ, where a bare VIOLAÇÃO
   descriptor means *breach of a duty* (banking, contracts) rather than rape.

Only VIOLAÇÃO is ambiguous. Every other adult term is unmistakably a sexual
offence, so the civil filter is applied to violação alone — running it over
LENOCÍNIO or COAÇÃO SEXUAL would drop real cases on an irrelevant test.

Spelling note: DGSI holds rulings under both post-1990 and pre-1990 orthography
(coação/coacção, extorsão/extorção). Vocabularies list every spelling in use;
matching is substring-based on the uppercased descriptor.
"""
from __future__ import annotations

# --- adult descriptor vocabularies -------------------------------------------
# Keyed by the brief's term. Each value is the set of substrings that identify
# it in a DGSI descriptor, across spellings and aggravated/attempted variants.
ADULT_MARKERS = {
    "coação sexual": ("COAÇÃO SEXUAL", "COACÇÃO SEXUAL", "COACAO SEXUAL"),
    "importunação sexual": ("IMPORTUNAÇÃO SEXUAL", "IMPORTUNACAO SEXUAL"),
    "abuso sexual de pessoa internada": ("ABUSO SEXUAL DE PESSOA INTERNADA",),
    "lenocínio": ("LENOCÍNIO", "LENOCINIO"),
    "abuso sexual de pessoa incapaz de resistência": (
        "ABUSO SEXUAL DE PESSOA INCAPAZ DE RESIST",
        "APROVEITAMENTO DE INCAPAZ",
    ),
}

# violação is handled separately — it is the one ambiguous term (see module docstring)
RAPE_MARKERS = ("VIOLAÇÃO", "VIOLACAO")
RAPE_DESC = {"VIOLAÇÃO", "CRIME DE VIOLAÇÃO", "VIOLAÇÃO AGRAVADA", "VIOLAÇÃO SEXUAL"}

# Substring tests, so singular forms also catch their plurals. The "DE"-less
# spellings are real: DGSI files rulings under both "ABUSO SEXUAL DE MENORES"
# and "ABUSO SEXUAL MENORES".
CHILD_MARKERS = (
    "ABUSO SEXUAL DE CRIANÇA", "ABUSO SEXUAL CRIANÇA",
    "ABUSO SEXUAL DE MENOR", "ABUSO SEXUAL MENOR",
    "PORNOGRAFIA DE MENOR", "PORNOGRAFIA DE CRIANÇA", "PORNOGRAFIA INFANTIL",
    "SEXUAIS COM ADOLESCENTE", "SEXUAL COM ADOLESCENTE",
    "LENOCÍNIO DE MENOR", "LENOCÍNIO MENOR",
    "ALICIAMENTO DE MENOR", "EXPLORAÇÃO SEXUAL DE MENOR",
    "EXPLORAÇÃO SEXUAL DE CRIANÇA", "PROSTITUIÇÃO DE MENOR",
    "ABUSO SEXUAL DE PESSOA EM SITUAÇÃO VULNER",
)

# a bare VIOLAÇÃO is *rape* only in a criminal/sexual context; these say "civil"
CIVIL_CONTEXT = (
    "RESPONSABILIDADE", "INTERMEDIAÇÃO", "DEVER DE INFORMA", "CONTRATO", "BANC",
    "FINANCEIR", "SEGURO", "PROPRIEDADE", "CONCORRÊNCIA", "DEVERES CONJUGAIS",
    "SEGREDO", "CORRESPONDÊNCIA", "DOMICÍLIO", "REGRAS DE SEGURANÇA",
    "REGRAS DE CONSTRUÇÃO", "CONTRADITÓRIO", "CADUCIDADE", "DIREITOS DE PERSONALIDADE",
    "DEVER DE LEALDADE", "INSOLVÊNCIA", "REVITALIZAÇÃO", "ARRENDAMENTO",
)
SEXUAL_CONTEXT = (
    "SEXUAL", "AUTODETERMINAÇÃO", "PUDOR", "LIBERDADE SEXUAL", "COAÇÃO SEXUAL",
    "OFENSA À LIBERDADE", "CRIMES CONTRA A LIBERDADE",
)
# Meio Processual values that indicate a *criminal* appeal
CRIMINAL_MEIO = ("PENAL", "RECURSO PENAL")
CIVIL_MEIO = ("REVISTA", "APELAÇÃO", "AGRAVO", "PROVIDÊNCIA", "EMBARGOS")

# What the pool builder queries DGSI for, covering both categories.
#
# These are deliberately wider than the scope: Domino matches a quoted phrase
# literally, folding accents but doing no stemming, so a term written out in
# full ("abuso sexual de crianças") silently misses every ruling filed under a
# different number or word order — "ABUSO SEXUAL DE CRIANÇA", "ABUSO SEXUAL
# MENORES". Enumerating the combinations is a losing game, so each term is a
# wildcard stem chosen for recall and precision is left to the markers above.
# Verified against the live index: "coaç* sexua*" returns exactly the union of
# the coação/coacção spellings.
#
# COMPLETENESS AUDIT — is the brief's term list missing anything?
# Checked against Código Penal Título I Cap. V (arts. 163-176-B, crimes contra a
# liberdade e autodeterminação sexual) and then against the live DGSI index:
#
#   * The brief's 6 adult + 11 minor terms cover the whole chapter bar two
#     articles: fraude sexual (167) and procriação artificial não consentida
#     (168). Both return ZERO rulings in DGSI, all courts, all years. No gap.
#   * Old penal-code names were the real risk — abuse from the 80s/90s is tried
#     today. "atentado ao pudor" has 151 rulings and "estupro" 20, but NONE fall
#     in 2020-2026: old facts tried now are filed under the modern descriptor,
#     so "abuso sexual*" already catches them. No gap.
#   * tráfico de pessoas (art. 160) is the one judgment call: 17 rulings in
#     range, 2 already in the pool via other descriptors, 3 sexual-exploitation
#     cases genuinely missing. DELIBERATELY EXCLUDED from the first pass — different
#     chapter (liberdade pessoal, sexual purpose rather than sexual offence),
#     and 3 rulings is 0.7% of the pool against a +/-8pt margin, not worth
#     re-drawing the sample. Left as an optional extension.
#   * mutilação genital feminina (1 ruling) and casamento forçado (1, filed
#     under RAPTO) likewise excluded — not sexual offences in the brief's sense.
#
# Precision note: these stems are intentionally over-broad. "aliciamento*" also
# matches employee poaching and "prostituiç*" adult prostitution; neither
# survives the markers above. Verified on the full in-scope pool: all 53 admitting
# descriptors are genuine sexual offences. The lone exception is bare VIOLAÇÃO,
# which is what _rape_is_civil() exists to filter.
SEARCH_TERMS = [
    "violaç*",                 # adults: violação (also the civil breach sense)
    "coaç* sexua*",            # adults: coação/coacção sexual
    "importunaç* sexua*",      # both sides
    "lenocínio*",              # both sides
    "abuso sexual*",           # abuso sexual de criança/menor/internada/incapaz/vulnerável
    "sexua* com adolescente*", # minors: actos sexuais com adolescentes
    "aliciamento*",            # minors
    "pornografia*",            # minors
    "prostituiç*",             # minors
    "exploraç* sexua*",        # minors — no descriptor in the index today (0 hits)
    "turismo sexual*",         # minors — no descriptor in the index today (0 hits)
]


def is_minor(descritores) -> bool:
    return any(any(m in d for m in CHILD_MARKERS) for d in descritores)


def _has(descritores, needles) -> bool:
    return any(any(n in d for n in needles) for d in descritores)


def adult_offences(descritores) -> list[str]:
    """Which unambiguous adult offence families this ruling is tagged with."""
    return [term for term, markers in ADULT_MARKERS.items()
            if _has(descritores, markers)]


def rape_descriptor_only(descritores) -> bool:
    """First-pass (no fetch): bare/explicit rape descriptor present."""
    if any(d in RAPE_DESC for d in descritores):
        return True
    if _has(descritores, SEXUAL_CONTEXT):
        return any("VIOLA" in d for d in descritores)
    return False


def adult_descriptor_only(descritores) -> bool:
    """First-pass (no fetch): any in-scope adult descriptor present."""
    return bool(adult_offences(descritores)) or rape_descriptor_only(descritores)


def _rape_is_civil(descritores, meio: str, sec: str) -> str | None:
    """Reason this violação hit is a civil *breach* rather than rape, or None."""
    if any(c in meio for c in CIVIL_MEIO):
        return f"civil meio processual ({meio})"
    if _has(descritores, CIVIL_CONTEXT) and not _has(descritores, SEXUAL_CONTEXT):
        return "civil-context descriptors"
    # STJ 2ª/6ª/7ª sections are civil; 3ª/5ª are criminal
    if sec and any(s in sec for s in ("2.", "2ª", "6.", "6ª", "7.", "7ª")) \
            and not any(c in meio for c in CRIMINAL_MEIO) \
            and not _has(descritores, SEXUAL_CONTEXT):
        return f"civil section ({sec})"
    return None


def classify(descritores, meio_processual: str = "", seccao: str = "") -> dict:
    """Full classification. Returns {categoria, is_scope, reason, offences}.

    categoria in {"Minors", "Adults", None}.
    offences lists the adult offence families matched (empty for Minors/reject).
    """
    if is_minor(descritores):
        return {"categoria": "Minors", "is_scope": True,
                "reason": "child descriptor", "offences": []}

    meio = (meio_processual or "").upper()
    sec = (seccao or "").upper()

    offences = adult_offences(descritores)
    if offences:
        # unambiguous sexual-offence descriptor — no civil filter needed
        return {"categoria": "Adults", "is_scope": True,
                "reason": "adult sexual offence (" + ", ".join(offences) + ")",
                "offences": offences}

    if not rape_descriptor_only(descritores):
        return {"categoria": None, "is_scope": False,
                "reason": "no in-scope descriptor", "offences": []}

    # rape candidate — now weed out civil "violação" (breach) false positives
    civil = _rape_is_civil(descritores, meio, sec)
    if civil:
        return {"categoria": None, "is_scope": False, "reason": civil, "offences": []}

    return {"categoria": "Adults", "is_scope": True,
            "reason": "rape (criminal)", "offences": ["violação"]}
