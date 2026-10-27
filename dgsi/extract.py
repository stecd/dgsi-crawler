"""Extract the fields that live in free text: the sentence (pena) and — the
research question at the core — whether it was suspended (suspensão de execução).

Two tiers:
  * regex first-pass (runs now, no API needed) — detects suspension and any
    "X anos de prisão" durations. Good recall on the explicit phrasings, with a
    confidence flag so downstream QA knows what to trust.
  * llm_extract() — the accurate pass (recorrente, condenação vs recurso, the
    fundamentações, the appeal chain). Requires the Anthropic API; raises a
    clear error until a key is configured, so the pipeline degrades gracefully.
"""
from __future__ import annotations
import os
import re

# --- regex first-pass --------------------------------------------------------
_SUSP = re.compile(
    r"suspens[ãa]o?\s+d[ae]\s+execu[çc][ãa]o\s+d[ae]\s+pena"
    r"|suspens[ao]\s+na\s+sua\s+execu[çc][ãa]o"
    r"|pena\s+(?:\w+\s+){0,3}suspens", re.I)

_NOT_SUSP = re.compile(r"n[ãa]o\s+(?:se\s+)?suspend|revoga[çc][ãa]o\s+da\s+suspens", re.I)

_PRISAO = re.compile(
    r"(\d{1,2})\s*anos?(?:\s+e\s+(\d{1,2})\s*meses)?\s+de\s+pris[ãa]o", re.I)

_SUSP_PERIOD = re.compile(
    r"suspens[ao]\s+(?:na\s+sua\s+execu[çc][ãa]o\s+)?por\s+(?:um\s+per[íi]odo\s+de\s+)?"
    r"(\d{1,2})\s*anos?", re.I)


def regex_pass(sumario: str, texto: str) -> dict:
    """Heuristic suspension + sentence detection. Confidence is 'high' only when
    the suspension phrasing is explicit."""
    blob = f"{sumario}\n{texto}"
    suspended = bool(_SUSP.search(blob))
    if suspended and _NOT_SUSP.search(blob):
        # both appear — ambiguous, let the LLM/QA decide
        conf = "low"
    else:
        conf = "high" if suspended else "medium"

    penas = [f"{a} anos" + (f" e {b} meses" if b else "")
             for a, b in _PRISAO.findall(blob)]
    period = _SUSP_PERIOD.search(blob)

    return {
        "suspensao_execucao": "sim" if suspended else "não/indeterminado",
        "suspensao_conf": conf,
        "penas_detectadas": "; ".join(dict.fromkeys(penas)) if penas else "",
        "suspensao_periodo_anos": period.group(1) if period else "",
    }


# --- LLM pass (accurate; needs API key) --------------------------------------
# Sanity guard only — NOT a truncation cap. ~500k chars is ~143k tokens, well
# inside the 1M window, and above the pilot's longest ruling (357k chars).
MAX_TEXT_CHARS = 500_000

LLM_SCHEMA = {
    "type": "object",
    "properties": {
        # --- added 16 Jul (trial pass): a DGSI sexual-offence ruling is not
        # necessarily a *conviction*. The pilot trial hit two rulings that are
        # in scope by descriptor but have no pena at all — one an appeal against
        # an acusação not being received, one about prisão preventiva (a coercion
        # measure, not a sentence). There is no execução to suspend, so scoring
        # them "não" would silently pad the denominator and drag the headline
        # rate down. They must be excluded from the rate, not counted against it.
        "tipo_decisao": {
            "type": "string",
            "enum": ["condenação", "absolvição", "interlocutória", "outra"],
            "description": "Só 'condenação' entra no cálculo da taxa de suspensão. "
                           "'interlocutória' = recurso sobre medida de coação, acusação, "
                           "nulidade, etc. — não há pena aplicada, logo nada a suspender."},

        "recorrente": {"type": "string",
                       "description": "quem recorre: arguido / Ministério Público / assistente / outro"},
        "condenacao_1a_inst": {"type": "string", "description": "pena na 1.ª instância"},
        "decisao_recurso": {"type": "string", "description": "decisão deste tribunal (mantida/reduzida/agravada/absolvido)"},
        "pena_final": {"type": "string"},
        "suspensao_execucao": {
            "type": "string", "enum": ["sim", "não", "não aplicável", "indeterminado"],
            "description": "'não aplicável' quando não há pena a suspender (tipo_decisao != "
                           "condenação). 'indeterminado' é para dúvida genuína na leitura — "
                           "os dois casos são distintos e não devem ser confundidos."},
        "suspensao_periodo_anos": {"type": "string"},
        "fundamentacao_facto": {"type": "string", "description": "resumo (1-2 frases)"},
        "fundamentacao_direito": {"type": "string", "description": "resumo (1-2 frases)"},
        "fonte_trecho": {"type": "string", "description": "trecho exato que suporta a suspensão"},

        # --- context fields: not on the original field list, but the research
        # question cannot be tested without them.
        "suspensao_condicoes": {
            "type": "string",
            "description": "Condições da suspensão: regime de prova, deveres, regras de "
                           "conduta, tratamento/acompanhamento. 'nenhuma' se a suspensão "
                           "for simples; '' se não houve suspensão."},
        "suspensao_legalmente_possivel": {
            "type": "string", "enum": ["sim", "não", "não aplicável", "indeterminado"],
            "description": "O art. 50.º CP só permite suspensão até 5 anos de prisão. "
                           "'não' se a pena final excede 5 anos (o tribunal não podia "
                           "suspender, independentemente do que entendesse). "
                           "'não aplicável' se não há pena."},
        "antecedentes_criminais": {
            "type": "string", "enum": ["primário", "com antecedentes", "reincidente", "indeterminado"],
            "description": "Antecedentes do arguido — o principal fundamento do juízo de "
                           "prognose favorável do art. 50.º"},
        "vitima_idade": {
            "type": "string",
            "description": "Idade da vítima à data dos factos, em escalão: '0-5', '6-11', "
                           "'12-13', '14-15', '16-17', 'adulto', 'indeterminado'. NÃO "
                           "registar nome nem qualquer dado identificativo."},
        # 'cônjuge/companheiro(a)/ex' added after the trial pass: without it the
        # violência-doméstica cases — the modal adult case — were being forced
        # into 'conhecido', which is actively misleading about the relationship.
        "vitima_relacao": {
            "type": "string",
            "description": "Relação vítima-arguido em categoria: 'pai/mãe', 'padrasto/"
                           "madrasta', 'outro familiar', 'cônjuge/companheiro(a)/ex', "
                           "'conhecido', 'posição de autoridade', 'desconhecido', "
                           "'indeterminado'. Categoria apenas — nunca nomes nem dados "
                           "identificativos."},
        # Victim fields are per-ruling but rulings are not per-victim. Where a
        # ruling covers several victims the age band and relation are an
        # approximation of one of them; this flag stops that being read as fact.
        "vitimas_multiplas": {
            "type": "string", "enum": ["sim", "não", "indeterminado"],
            "description": "'sim' se o acórdão abrange mais do que uma vítima. Nesse caso "
                           "vitima_idade e vitima_relacao são aproximações (a vítima dos "
                           "crimes mais graves) e não devem ser lidos como exaustivos."},
        "pena_acessoria": {
            "type": "string",
            "description": "Penas acessórias aplicadas (p. ex. proibição de exercer "
                           "profissão/funções com menores, proibição de contacto). "
                           "'nenhuma' se não foram aplicadas."},
    },
    "required": ["tipo_decisao", "recorrente", "suspensao_execucao", "pena_final",
                 "suspensao_legalmente_possivel", "antecedentes_criminais",
                 "fonte_trecho"],
}

# fonte_trecho is required because it is the only field that can be checked
# mechanically: it must appear verbatim in the ruling. The trial pass grepped
# all 10 back against source and all 10 matched, which is what licenses trusting
# the fields that cannot be checked that way. Keep verifying it on every pass.


def llm_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def llm_extract(ruling: dict, model: str = "claude-sonnet-5") -> dict:
    """Accurate extraction via the Anthropic API. Raises if no key/package."""
    if not llm_available():
        raise RuntimeError("ANTHROPIC_API_KEY not set — LLM extraction unavailable. "
                           "Set the key to enable the accurate pass.")
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("`anthropic` package not installed (pip install anthropic).") from e

    client = anthropic.Anthropic()
    text = ruling.get("texto_integral") or ruling.get("sumario") or ""
    # Do NOT truncate. The decisão — where suspensão is actually pronounced —
    # sits at the end of a ruling, so a head-cap silently drops the evidence:
    # measured on the pilot, an earlier 24k-char cap discarded 73% of the text
    # and hid the only suspensão mention in 12 of 96 rulings, biasing the rate
    # down. Rulings run to ~357k chars (~100k tokens) against a 1M window, so
    # they fit whole. Raise rather than send a quietly partial ruling.
    if len(text) > MAX_TEXT_CHARS:
        raise RuntimeError(
            f"ruling text is {len(text):,} chars, over the {MAX_TEXT_CHARS:,} guard — "
            "do not silently truncate; split the ruling or raise the guard deliberately.")
    prompt = (
        "És um assistente jurídico. Extrai os campos do acórdão penal português abaixo. "
        "Foca-te em: quem recorre, a pena, e sobretudo se a pena foi SUSPENSA na sua execução "
        "(suspensão de execução de pena). Devolve os dados via a ferramenta.\n\n"
        f"DESCRITORES: {', '.join(ruling.get('descritores', []))}\n\n"
        f"SUMÁRIO:\n{ruling.get('sumario','')}\n\nTEXTO:\n{text}"
    )
    resp = client.messages.create(
        model=model, max_tokens=1024,
        tools=[{"name": "extrair", "description": "Regista os campos extraídos.",
                "input_schema": LLM_SCHEMA}],
        tool_choice={"type": "tool", "name": "extrair"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input
    raise RuntimeError("LLM returned no structured output")
