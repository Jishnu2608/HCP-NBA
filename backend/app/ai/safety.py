"""Shared deterministic safety checks on model text. No model judges a model.

Ported from the Case Brief evaluation gate (`evals/case_brief/checks.py`, prompt v10) and
generalised: the checks read the operation's context items instead of an evaluation case, and
the drug vocabulary comes from the subject's own record plus a fixed list. The evaluation keeps
its own frozen copy so historical results stay reproducible.

Every operation's output runs through `check_text_fields`; operations add their own checks on
top and cannot skip these. A finding records a fixed code and the field, never model text.
"""

import re
from datetime import date

from app.ai.contracts import AuthorizedContext, ContextItem, GateFinding

MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sept", "sep"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        1,
    )
    for m in names
}
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
MDY = re.compile(rf"\b({_MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)
DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MON})\.?,?\s+(\d{{4}})\b", re.I)
MY = re.compile(rf"\b({_MON})\.?,?\s+(\d{{4}})\b", re.I)
YM = re.compile(r"\b(\d{4}-\d{2})\b(?!-\d)")
MD = re.compile(rf"\b({_MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?!,?\s*\d{{4}})", re.I)
MDD = re.compile(
    rf"\b({_MON})\.?\s+(\d{{1,2}})(,?\s+and\s+|,\s+)(\d{{1,2}})\b(?!\s*(?:days?|%|{_MON}))",
    re.I,
)
DM = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MON})\b(?!\.?,?\s*\d{{4}})", re.I)
# Evidence ids, record ids (PAT_00012, HCP_0042, CNT_014) and hyphenated codes.
IDS = re.compile(r"\b(?:ctx:[a-z_]+:\d+|[A-Z]{2,}_\d+|[A-Z]{2,}(?:-[A-Z0-9]+)+)\b")
NUMBER = re.compile(r"(?<![\w.:])\d+(?:\.\d+)?")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
URL = re.compile(r"\b(?:https?://|www\.)\S+", re.I)

DRUG_TERMS = [
    "metformin", "insulin", "lisinopril", "atorvastatin", "warfarin", "amlodipine", "statin",
    "aspirin", "ibuprofen", "albuterol", "levothyroxine", "sertraline", "omeprazole",
    "apixaban", "semaglutide", "prednisone", "losartan", "rosuvastatin", "simvastatin",
    "glipizide", "empagliflozin", "dapagliflozin", "sitagliptin", "valsartan",
]  # fmt: skip
CONDITION_TERMS = [
    "diabetes", "hypertension", "heart failure", "heart disease", "asthma", "copd",
    "depression", "cancer", "stroke", "kidney", "cholesterol", "arrhythmia", "anxiety",
    "thyroid", "obesity", "infection",
]  # fmt: skip
DOSING = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:mg|mcg|µg|ml|iu|units?|tablets?|pills?|capsules?)\b", re.I
)
ADVICE = re.compile(
    r"\b(?:dos(?:e|es|age|ing)|doubl\w+|halv\w+|titrat\w+|stop(?:ping)? taking|skip\w*"
    r" (?:a |the )?(?:dose|medic\w+)|discontinu\w+|switch\w* (?:to|medic\w+)|prescrib\w+)\b",
    re.I,
)
DIAGNOSIS = re.compile(
    r"\b(?:diagnos\w+|suffers? from|likely (?:has|have)|symptom\w*|side effects?"
    r"|contraindicat\w+|interactions?|efficacy|effective(?:ness)?|clinically|prognosis|toxic\w*)\b",
    re.I,
)
UNSAFE_MEDICATION = re.compile(
    r"\b(?:tablets?|pills?|capsules?|dos(?:e|es|age|ing)|\d+\s?mg|doubl\w*|stronger|weaker"
    r"|take two|stop(?:ping)? (?:taking|the medic\w*)|side effects?|symptoms?|dizz\w*|cough\w*"
    r"|heart failure|overdose)\b",
    re.I,
)
RECOMMENDATION = re.compile(r"\brecommend\w*", re.I)
NEGATED = re.compile(r"\b(?:no|not|none|without|lack|lacks|absence|absent|missing)\b", re.I)
# Model text about consent or permitted channels: only the server states consent.
CONSENT_CLAIM = re.compile(
    r"\b(?:consent\w*|opt(?:ed)?[- ]?(?:in|out)|permission\w*|permitted|authori[sz]\w*"
    r"|(?:not )?allowed to (?:contact|call|email|text|message)"
    r"|allows? (?:email|sms|phone|contact))\b",
    re.I,
)
# Claims that something was done or changed: the model only describes, it never acts.
ACTION_CLAIM = re.compile(
    r"\b(?:marked (?:as )?(?:closed|resolved|complete\w*)|case (?:is |was |has been )?closed"
    r"|has been (?:updated|changed|resolved|closed|escalated|sent|approved|cancel\w*)"
    r"|i (?:have|will|am)\b|status (?:changed|set|updated))",
    re.I,
)


def normalize_dates(text: str, today: date) -> str:
    def iso(y: int, m: int, d: int) -> str:
        try:
            return date(y, m, d).isoformat()
        except ValueError:
            return f"{y:04d}-{m:02d}-{d:02d}"

    text = MDD.sub(lambda g: f"{g[1]} {g[2]}{g[3]}{g[1]} {g[4]}", text)
    text = MDY.sub(lambda g: iso(int(g[3]), MONTHS[g[1].lower()], int(g[2])), text)
    text = DMY.sub(lambda g: iso(int(g[3]), MONTHS[g[2].lower()], int(g[1])), text)
    text = MD.sub(lambda g: iso(today.year, MONTHS[g[1].lower()], int(g[2])), text)
    text = DM.sub(lambda g: iso(today.year, MONTHS[g[2].lower()], int(g[1])), text)
    return MY.sub(lambda g: f"{int(g[2]):04d}-{MONTHS[g[1].lower()]:02d}", text)


def facts(text: str, today: date) -> tuple[set[str], set[float], set[str]]:
    """(dates, numbers, ids) in text. Dates and ids are removed before numbers are read."""
    text = normalize_dates(text, today)
    dates = {"-".join(t) for t in ISO.findall(text)}
    dates |= set(YM.findall(ISO.sub(" ", text)))
    ids = set(IDS.findall(text))
    rest = IDS.sub(" ", YM.sub(" ", ISO.sub(" ", text)))
    return dates, {float(n) for n in NUMBER.findall(rest)}, ids


def _known_date(d: str, known: set[str]) -> bool:
    return d in known or (len(d) == 7 and any(k.startswith(d) for k in known))


def terms(text: str, vocab: list[str]) -> set[str]:
    low = text.lower()
    return {t for t in vocab if t and re.search(rf"\b{re.escape(t)}\b", low)}


def allowed_numbers(items: list[ContextItem], today: date) -> set[float]:
    """Numbers in the items, their percent forms, item counts, years and day gaps."""
    dates, nums, _ = facts(" ".join(i.text for i in items), today)
    allowed = set(nums)
    allowed |= {round(n * 100, 4) for n in nums if 0 < n <= 1}
    allowed |= {float(i) for i in range(1, len(items) + 1)}
    full = {d for d in dates | {today.isoformat()} if len(d) == 10}
    allowed |= {float(d[:4]) for d in full}
    days = sorted(date.fromisoformat(d) for d in full)
    allowed |= {float(abs((a - b).days)) for a in days for b in days}
    return allowed


def _fragments(text: str, size: int = 24) -> set[str]:
    words = re.sub(r"\s+", " ", text.strip().lower())
    if len(words) <= size:
        return {words} if len(words) >= 12 else set()
    return {words[i : i + size] for i in range(0, len(words) - size + 1, size // 2)}


def check_text_fields(
    ctx: AuthorizedContext,
    fields: dict[str, tuple[str, list[str]]],
    instructions: list[str] | None = None,
) -> tuple[list[GateFinding], int]:
    """Shared checks over model-written text. `fields` maps an output field to (text, the
    citation ids it relies on). Returns (findings, miscited statements).

    Facts are judged against what the model was given (`ctx.model_items`): a date, number, id,
    drug or condition the model could not have read is fabricated, whatever the record holds."""
    items = ctx.model_items
    item_text = {i.id: i.text for i in items}
    ctx_all = " ".join(item_text.values())
    ctx_low = ctx_all.lower()
    today = ctx.as_of
    ctx_dates, ctx_nums, ctx_ids = facts(ctx_all, today)
    allowed = allowed_numbers(items, today)
    known_dates = ctx_dates | {today.isoformat()}
    vocab = sorted({t.lower() for t in [*DRUG_TERMS, *ctx.vocabulary] if t})
    withheld = (
        set().union(*(_fragments(t) for t in ctx.withheld_texts)) if ctx.withheld_texts else set()
    )
    names = {n.lower() for n in ctx.identifiers if n}
    rec_categories = {"nba"} | {
        cat
        for cat, words in _category_terms(ctx).items()
        if any(RECOMMENDATION.search(w) for w in words)
    }

    findings: list[GateFinding] = []
    miscited = 0

    def flag(kind: str, where: str) -> None:
        findings.append(GateFinding(kind=kind, where=where))

    for where, (text, cites) in fields.items():
        low = text.lower()
        squashed = re.sub(r"\s+", " ", low)
        # Citations: only ids the model was given.
        for cid in cites:
            if cid not in item_text:
                flag("invalid_citation", where)
        # Fabricated facts.
        dates, nums, ids = facts(text, today)
        if any(not _known_date(d, known_dates) for d in dates):
            flag("fabricated_date", where)
        if nums - allowed:
            flag("fabricated_number", where)
        if any(not i.startswith("ctx:") for i in ids - ctx_ids):
            flag("fabricated_id", where)
        if terms(text, vocab) - terms(ctx_all, vocab):
            flag("fabricated_drug", where)
        if terms(text, CONDITION_TERMS) - terms(ctx_all, CONDITION_TERMS):
            flag("fabricated_condition", where)
        # Clinical content.
        for pattern, kind in (
            (DOSING, "clinical_claim:dosing"),
            (ADVICE, "clinical_claim:dosing_advice"),
            (DIAGNOSIS, "clinical_claim:clinical"),
        ):
            if any(m.group().lower() not in ctx_low for m in pattern.finditer(text)):
                flag(kind, where)
        if UNSAFE_MEDICATION.search(text):
            flag("unsafe_medication", where)
        # A recommendation may be named only if an item records one: an item of a category
        # whose own words name a recommendation (any source, not one consumer's item kind).
        if not any((i.category or i.kind) in rec_categories for i in items):
            for sentence in re.split(r"[.;\n]", text):
                if RECOMMENDATION.search(sentence) and not NEGATED.search(sentence):
                    flag("fabricated_recommendation", where)
                    break
        # Consent and permissions are the server's to state.
        if CONSENT_CLAIM.search(text):
            flag("consent_claim", where)
        # The model describes; it never claims an action was taken.
        if ACTION_CLAIM.search(text):
            flag("action_claim", where)
        # Leaks: contact details, links, identifiers and withheld free text.
        if EMAIL.search(text) or PHONE.search(ISO.sub(" ", text)):
            flag("leak:contact", where)
        if URL.search(text):
            flag("leak:link", where)
        if any(re.search(rf"\b{re.escape(n)}\b", low) for n in names):
            flag("leak:identifier", where)
        if any(f in squashed for f in withheld):
            flag("leak:free_text", where)
        # Absence claims, statuses and codes must match the context's own record of them.
        findings.extend(check_absence(ctx, text, where, cites))
        findings.extend(check_claims(ctx, text, where, cites))
        findings.extend(check_instruction_leak(text, instructions or [], where))
        # Grounding: a fact from the items must sit in the items this field cites.
        cited = " ".join(item_text.get(c, "") for c in cites)
        d_c, n_c, i_c = facts(cited, today)
        n_c = n_c | {round(n * 100, 4) for n in n_c if 0 < n <= 1}
        if (
            (dates & ctx_dates) - d_c
            or (ids & ctx_ids) - i_c
            or (nums & ctx_nums) - n_c
            or (terms(text, vocab) & terms(ctx_all, vocab)) - terms(cited, vocab)
        ):
            miscited += 1
    return findings, miscited


# --- Absence, status, codes, injection and instruction leaks (shared, operation-independent) ---

# A sentence that says something is not recorded, not available or missing.
ABSENCE = re.compile(
    r"\b(?:not|never|no|none|nothing|\w+n't)\b[^.;\n]{0,48}?\b(?:recorded|documented|on file"
    r"|available|logged|listed|found|captured|provided|present|known|registered|entered|find"
    r"|locate|located|identify|identified|show|shows|showing|indicate|indicates|exist|exists)\b"
    r"|\bunable to (?:find|locate|identify|confirm|see)\b"
    r"|\b(?:found|finds|see|sees|shows?) no\b"
    r"|\b(?:no|without|zero)\s+(?:record|records|data|information|history|evidence|entries)\b"
    r"|\b(?:has|have|had|there (?:is|are|was|were)) no (?:\w+ ){0,2}(?:record|records|data"
    r"|information|history|entry|entries)\b|\bmissing\b|\bnone (?:is|are|was|were)\b",
    re.I,
)
# Inflections a category word may take in a sentence ("refill" -> "refills", "refilled").
INFLECT = r"(?:s|es|ed|d|ing)?"
# "Approved content", "the approved cost-support offer content": MLR-approved material named as
# material (up to three describing words before the noun), not a claim about a decision status.
APPROVED_CONTENT = re.compile(
    r"\bapproved (?:[\w-]+ ){0,3}?(?:content|material|materials|module|modules|wording|offer"
    r"|offers|leaflet|leaflets)\b",
    re.I,
)
# "No open recommendation", "no recent outreach": "no" plus at most two qualifiers, then a
# category word (checked per category). "No response to outreach" is not an absence claim.
QUALIFIERS = (
    r"(?:(?:open|active|current|recent|prior|previous|known|recorded|documented|logged"
    r"|registered|available)\s+){0,2}"
)
# Status words that name a decision or workflow state; the model may repeat them only as the
# items state them. Words with everyday meanings ("sent", "pending") are deliberately absent.
STATUS_TERMS = (
    "approved", "blocked", "rejected", "expired", "withdrawn", "superseded", "deferred",
    "held out", "ready for review", "awaiting hcp", "awaiting the hcp", "in progress",
    "changes requested",
)  # fmt: skip
STATUS = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in STATUS_TERMS) + r")\b", re.I)
# A status is asserted ("was approved", "status is blocked", "remains in progress"), not used as
# an adjective ("the approved cost-support content"): only assertions are checked.
STATUS_ASSERTION = re.compile(
    r"\b(?:is|was|were|are|been|be|being|now|remains?|marked(?: as)?|status(?: is| was)?:?)\s+"
    r"(?:\w+\s+)?(?P<term>" + "|".join(re.escape(t) for t in STATUS_TERMS) + r")\b",
    re.I,
)
# A status used as an adjective of an item ("the blocked reminder", "an expired module"). Only
# single-word decision statuses; "approved content" (material) is removed before this runs.
ADJECTIVE_TERMS = ("approved", "blocked", "rejected", "expired", "withdrawn", "superseded",
                   "deferred")  # fmt: skip
STATUS_ADJECTIVE = re.compile(
    r"\b(?:the|a|an|this|that|its|their|any|each|every)\s+(?P<term>"
    + "|".join(ADJECTIVE_TERMS)
    + r")\s+(?!by\b|for\b|on\b|in\b|at\b|to\b|and\b|or\b)[a-z]",
    re.I,
)
CODE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
# Instructions hidden in data or a question: flagged, never obeyed.
INJECTION = {
    "instruction_override": re.compile(
        r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,30}\b(?:instructions?|rules?|prompt"
        r"|guidelines?|polic(?:y|ies))\b",
        re.I,
    ),
    "role_switch": re.compile(
        r"\b(?:you are now|act as (?:an?|the|my) \w+|pretend (?:to be|you are)|from now on you)\b",
        re.I,
    ),
    "prompt_extraction": re.compile(
        r"\b(?:system prompt|developer (?:message|prompt)|hidden (?:context|instructions)"
        r"|(?:reveal|print|show|repeat) (?:your|the) (?:instructions|prompt|context))\b",
        re.I,
    ),
    "tool_call": re.compile(
        r"<\s*/?\s*(?:tool|function|system)\b|\"tool_calls?\"|function_call", re.I
    ),
    "privilege_escalation": re.compile(
        r"\b(?:as an? (?:admin|administrator|compliance)|grant (?:me|yourself|access)|bypass"
        r"|elevate|escalate (?:my |your )?(?:privileges?|access)|all patients?)\b",
        re.I,
    ),
    "exfiltration": re.compile(
        r"\b(?:send|post|upload|email) (?:this|it|the data|everything) to\b", re.I
    ),
}


ECHO_CODES = frozenset({"instruction_override", "role_switch", "prompt_extraction", "tool_call"})


def screen_injection(text: str) -> list[str]:
    """Codes of instruction-like patterns in untrusted text (a question, retrieved data, model
    output). Detection is a signal for refusal or labelling, never permission to act."""
    return sorted(code for code, pattern in INJECTION.items() if pattern.search(text or ""))


def _category_terms(ctx: AuthorizedContext) -> dict[str, tuple[str, ...]]:
    from app.ai.sources import UnknownSource, get_source

    try:
        source = get_source(ctx.source)
    except UnknownSource:
        return {}
    return {c.name: c.terms or (c.name.replace("_", " "),) for c in source.categories}


# A negated fact an item itself states ("not yet filled", "no response"): its word supports an
# absence claim about the same thing when the field cites that item.
ITEM_NEGATION = re.compile(r"\b(?:not yet|not|no|never)\s+(\w+)", re.I)


def _stem(word: str) -> str:
    return word.lower()[:4]


def _term_hits(terms: dict[str, tuple[str, ...]], text: str) -> list[tuple[int, str]]:
    """(position, category) of every category word in text, in any common inflection."""
    low = text.lower()
    return sorted(
        (m.start(), cat)
        for cat, words in terms.items()
        for w in words
        for m in re.finditer(rf"\b{re.escape(w)}{INFLECT}\b", low)
    )


AFTER_WINDOW = 40  # characters after an absence phrase in which its object may follow


def _targets(terms, sentence: str, start: int, end: int) -> list[str]:
    """The categories an absence phrase is about: the category words inside the phrase; if it
    names none, the first category word that closely follows it ("could not find any refills");
    otherwise the nearest category word before it in the same sentence ("... indicating none
    are recorded"). Words elsewhere in the sentence are not its target."""
    hits = _term_hits(terms, sentence)
    inside = sorted({cat for pos, cat in hits if start <= pos < end})
    if inside:
        return inside
    after = [cat for pos, cat in hits if end <= pos < end + AFTER_WINDOW]
    if after:
        return after[:1]
    before = [cat for pos, cat in hits if pos < start]
    return before[-1:]


def _corroborated(
    ctx: AuthorizedContext, category: str, phrase: str, sentence: str, cites: list[str]
) -> bool:
    """A cited item of this category states the same negated fact (the absence phrase shares a
    word stem with a negation in that item, and any medicine the sentence names is the item's):
    "Metformin ... no fill recorded" with "Therapy: metformin ... not yet filled"."""
    words = {_stem(w) for w in re.findall(r"[a-z]{3,}", phrase.lower())}
    # A medicine named in the sentence must be the one the item is about.
    vocab = sorted({t.lower() for t in [*DRUG_TERMS, *ctx.vocabulary] if t})
    named = terms(sentence, vocab)
    for item in ctx.model_items:
        if item.id not in cites or (item.category or item.kind) != category:
            continue
        if named and not named <= terms(item.text, vocab):
            continue
        negated = {_stem(m.group(1)) for m in ITEM_NEGATION.finditer(item.text)}
        if words & negated:
            return True
    return False


def check_absence(
    ctx: AuthorizedContext, text: str, where: str, cites: list[str] | None = None
) -> list[GateFinding]:
    """Absence claims must be supported by the record, never by what a context left out.

    Each absence phrase ("not recorded", "no open recommendation", "missing", ...) is mapped to
    the categories it is about (`_targets`). It is supported when every target category was read
    and holds nothing (`verified_empty`), or when a cited item of that category itself states
    the negated fact (`_corroborated`). Otherwise it is unsupported (the category was excluded,
    not requested, truncated, stale, unavailable or is present), and an absence phrase with no
    identifiable target cannot be verified. Both are rejected; the template stands."""
    from app.ai.contracts import ABSENCE_SUPPORTED

    cites = list(cites or [])
    findings: list[GateFinding] = []
    terms = _category_terms(ctx)
    states = {c.category: c.state for c in ctx.categories}
    bare_words = "|".join(
        sorted({re.escape(w) for words in terms.values() for w in words}, key=len, reverse=True)
    )
    bare = negated = None
    if bare_words:
        # "No open recommendation", "no recorded fills", "zero refills".
        bare = re.compile(rf"\b(?:no|zero)\s+{QUALIFIERS}(?:{bare_words}){INFLECT}\b", re.I)
        # "Never refilled", "has not been refilled", "has not had any outreach".
        negated = re.compile(
            rf"\b(?:not|never|\w+n't)\s+(?:(?:yet|been|had|any|ever|once|a|an)\s+){{0,3}}"
            rf"(?:{bare_words}){INFLECT}\b",
            re.I,
        )
    for sentence in re.split(r"[.;\n!?]", text):
        spans = [(m.start(), m.end()) for m in ABSENCE.finditer(sentence)]
        for pattern in (bare, negated):
            if pattern is not None:
                spans += [(m.start(), m.end()) for m in pattern.finditer(sentence)]
        for start, end in sorted(set(spans)):
            targets = _targets(terms, sentence, start, end)
            if not targets:
                findings.append(GateFinding(kind="unverifiable_absence", where=where))
                continue
            phrase = sentence[start:end]
            for cat in targets:
                state = states.get(cat)
                if state in ABSENCE_SUPPORTED or _corroborated(ctx, cat, phrase, sentence, cites):
                    continue
                kind = f"unsupported_absence:{state.value if state else 'unknown'}"
                findings.append(GateFinding(kind=kind, where=where))
    return findings


def _statuses(text: str) -> set[str]:
    return {m.group().lower() for m in STATUS.finditer(APPROVED_CONTENT.sub("content", text))}


def check_claims(
    ctx: AuthorizedContext, text: str, where: str, cites: list[str] | None = None
) -> list[GateFinding]:
    """Statuses and coded reasons may be repeated only as the items state them.

    A status, asserted ("was approved", "status is blocked") or used as an adjective of an item
    ("the blocked reminder"), must be stated by an item: absent from every item it is fabricated
    (or contradicts the statuses the items record). When the field cites items that state a
    status, the claimed status must be one of theirs: the cited record, at the version this
    context holds, is the one the claim is about. A code (snake_case, as reason and gate codes
    are written) absent from every item is fabricated."""
    findings: list[GateFinding] = []
    # "approved content" names material, not a decision status.
    item_text = APPROVED_CONTENT.sub("content", " ".join(i.text for i in ctx.model_items).lower())
    stated = _statuses(item_text)
    cited_ids = set(cites or [])
    cited_stated: set[str] = set()
    for i in ctx.model_items:
        if i.id in cited_ids:
            cited_stated |= _statuses(i.text.lower())
    for sentence in re.split(r"[.;\n!?]", APPROVED_CONTENT.sub("content", text)):
        if NEGATED.search(sentence):
            continue
        claimed = [m.group("term").lower() for m in STATUS_ASSERTION.finditer(sentence)]
        claimed += [m.group("term").lower() for m in STATUS_ADJECTIVE.finditer(sentence)]
        for term in claimed:
            if term not in stated:
                kind = "contradiction:status" if stated else "fabricated_status"
                findings.append(GateFinding(kind=kind, where=where))
            elif cited_stated and term not in cited_stated:
                findings.append(GateFinding(kind="contradiction:status", where=where))
    codes = {c for c in CODE.findall(text) if not c.startswith("ctx")}
    if codes - set(CODE.findall(item_text)):
        findings.append(GateFinding(kind="fabricated_code", where=where))
    return findings


def check_instruction_leak(text: str, instructions: list[str], where: str) -> list[GateFinding]:
    """Model output that repeats the operation's own instructions, or carries instruction-like
    patterns, is rejected."""
    findings: list[GateFinding] = []
    low = re.sub(r"\s+", " ", text.lower())
    for block in instructions:
        words = re.sub(r"\s+", " ", block.lower())
        size = 40
        if any(words[i : i + size] in low for i in range(0, max(len(words) - size, 0) + 1, 20)):
            findings.append(GateFinding(kind="leak:instructions", where=where))
            break
    echoed = set(screen_injection(text)) & ECHO_CODES
    if echoed:
        findings.append(GateFinding(kind="injection_echo", where=where))
    return findings
