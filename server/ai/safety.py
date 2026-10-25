"""Keyword lists and text checks shared by the AI services (PRD 9.2, 9.9, 15.3).

Everything here is pure and synchronous. The crumb validator (`validate_crumb_text`) lives here so
`profiles.py` (openers) and `crumbs.py` (crumbs) can share it without importing each other.
"""
from __future__ import annotations

import re

from models import Profile

EM_DASH = "\u2014"

# PRD 6.4 quick toggles, each expanded to a keyword list. Free text off-limits terms that match a
# toggle name expand the same way; anything else is matched as a literal phrase, and a single word
# of 4+ letters also as a prefix ("japan" blocks "Japanese", "climb" blocks "climber").
OFF_LIMITS_TOGGLES: dict[str, list[str]] = {
    "work": [
        "work", "job", "jobs", "career", "careers", "boss", "office", "coworker", "coworkers",
        "employer", "employee", "company", "manager", "workplace", "internship", "intern", "profession",
        "colleague", "colleagues", "promotion", "fired", "laid off", "layoff", "hiring", "resume",
        # occupations and employers: "a product designer at a startup" is work talk without the word "work"
        "startup", "start-up", "founder", "cofounder", "co-founder", "entrepreneur", "engineer",
        "engineering", "developer", "programmer", "designer", "consultant", "analyst", "freelance",
        "freelancer", "client", "clients", "customer", "customers", "team", "role", "industry",
        "day job", "nine to five", "9 to 5", "side hustle", "business", "salary", "paycheck",
    ],
    "family": [
        "family", "families", "mom", "mother", "dad", "father", "parent", "parents", "sibling",
        "siblings", "brother", "sister", "son", "daughter", "kid", "kids", "child", "children",
        "wife", "husband", "grandma", "grandpa", "grandmother", "grandfather", "cousin", "aunt",
        "uncle", "relatives",
    ],
    "dating history": [
        "dating history", "ex", "exes", "ex-boyfriend", "ex-girlfriend", "boyfriend", "girlfriend",
        "breakup", "broke up", "divorce", "divorced", "past relationship", "last relationship",
        "relationship", "relationships", "dating life", "dating apps", "dated",
    ],
    "politics": [
        "politics", "political", "election", "elections", "vote", "voting", "voted", "democrat",
        "democrats", "republican", "republicans", "liberal", "conservative", "president",
        "congress", "senate", "government", "campaign",
    ],
    "religion": [
        "religion", "religious", "church", "god", "faith", "pray", "prayer", "mosque", "temple",
        "synagogue", "bible", "quran", "torah", "christian", "muslim", "jewish", "hindu",
        "buddhist", "atheist", "spiritual",
    ],
    "health": [
        "health", "healthy", "sick", "illness", "disease", "diagnosis", "diagnosed", "doctor",
        "hospital", "therapy", "therapist", "medication", "medical", "surgery", "injury",
        "injured", "hurt", "concussion", "sprain", "sprained", "fracture", "fractured", "acl", "cancer", "anxiety", "depression", "mental health", "diabetes", "disability",
        "pregnant", "pregnancy", "rehab", "adhd", "chronic",
    ],
}

# PRD 9.2 sensitive categories: dropped from profiles even when mentioned. Narrower than the health
# toggle on purpose ("wants to become a doctor" is a goal, not health information).
SENSITIVE_KEYWORDS: list[str] = [
    # health
    "illness", "disease", "diagnosis", "diagnosed", "therapy", "therapist", "medication",
    "surgery", "cancer", "anxiety", "depression", "mental health", "diabetes", "disability",
    "pregnant", "pregnancy", "rehab", "adhd", "autism", "chronic",
    # injuries ("hurt my knee" is health information too); see also _INJURY_RE
    "injury", "injured", "concussion", "sprained", "fractured", "torn ligament", "acl", "physical therapy",
    # religion
    *OFF_LIMITS_TOGGLES["religion"],
    # politics
    "politics", "political", "election", "democrat", "democrats", "republican", "republicans",
    "liberal", "conservative",
    # sexuality
    "sexuality", "sexual orientation", "gay", "lesbian", "bisexual", "queer", "transgender",
    "coming out", "came out",
    # immigration status
    "immigration", "immigrant", "visa", "green card", "undocumented", "citizenship", "deported",
    "deportation", "asylum", "refugee",
    # finances
    "debt", "debts", "salary", "loan", "loans", "bankrupt", "bankruptcy", "income", "mortgage",
    "net worth", "credit score", "paycheck",
    # criminal history
    "arrested", "arrest", "jail", "prison", "convicted", "criminal record", "felony",
    "probation",
    # trauma
    "trauma", "traumatic", "abuse", "abused", "assault", "assaulted", "grief", "funeral",
    "passed away", "suicide", "self-harm", "ptsd",
]

# PRD 15.3: no sexual, body or appearance content in any mode.
UNSAFE_KEYWORDS: list[str] = [
    "sex", "sexy", "sexual", "sexually", "naked", "nude", "nudes", "kiss", "kissing", "make out",
    "making out", "hook up", "hooking up", "hookup", "body", "bodies", "attractive",
    "attraction", "handsome", "good looking", "good-looking", "your looks", "appearance",
    "outfit", "wearing", "weight", "curves", "chest", "abs", "lingerie", "underwear",
    "turn on", "turned on", "turn-on", "turn-ons", "seduce", "seductive", "erotic", "porn",
    "sleep with", "sleeping with", "one night stand", "bedroom", "fetish", "horny",
    "body count", "physique",
]

# "hurt my knee", "broke their wrist", "tore my ACL": an injury verb plus a body part. Kept as a
# pattern so "broke my phone" or "hurt my feelings" are not health information.
_INJURY_RE = re.compile(
    r"\b(?:hurt|hurts|hurting|injur(?:e|ed|es|ing)|broke|broken|break(?:s|ing)?|tore|torn|tear(?:s|ing)?|"
    r"sprain(?:ed|s|ing)?|twist(?:ed|s|ing)?|fractur(?:e|ed|es|ing)|pull(?:ed|s|ing)?|blew out|blown out|"
    r"messed up|wrecked)\s+(?:my|their|his|her|your|a|an|the|both)?\s*(?:left\s+|right\s+|lower\s+)?"
    r"(?:knee|knees|ankle|ankles|back|shoulder|shoulders|wrist|wrists|arm|arms|leg|legs|hip|hips|neck|"
    r"foot|feet|hand|hands|elbow|elbows|acl|mcl|meniscus|hamstring|hamstrings|rib|ribs|collarbone|"
    r"finger|fingers|toe|toes|spine|achilles|calf|groin|head|jaw|nose|muscle|ligament)\b",
    re.IGNORECASE,
)

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF‍️]"
)
_INLINE_TAG_RE = re.compile(r"<[^<>]{1,40}>")  # TTS timing tags such as "<short pause>"
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")


def clean_text(text: str) -> str:
    """Replace em dashes with commas, drop emojis and collapse whitespace."""
    text = text.replace(f" {EM_DASH} ", ", ").replace(EM_DASH, ", ")
    text = _EMOJI_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    return re.sub(r",\s*,", ",", text)


def word_count(text: str) -> int:
    return len(_WORD_RE.findall(_INLINE_TAG_RE.sub(" ", text)))


def truncate_words(text: str, max_words: int, max_chars: int | None = None) -> str:
    """Cut `text` to at most `max_words` words (and `max_chars` characters) at a word boundary."""
    words = text.split()
    out = " ".join(words[:max_words])
    if max_chars is not None and len(out) > max_chars:
        out = out[:max_chars].rsplit(" ", 1)[0] if " " in out[:max_chars] else out[:max_chars]
    return out.rstrip(" ,;:")


def first_sentence(text: str, max_chars: int = 200) -> str:
    text = clean_text(text)
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    sentence = match.group(1) if match else text
    return truncate_words(sentence, 40, max_chars)


PREFIX_MARK = "*"  # a trailing "*" on a keyword matches any word that starts with it


_ALNUM = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")
# Endings a keyword may take: "job" -> "jobs", "vote" -> "voted", "injury" -> "injuries", "sexy" -> "sexiest".
_INFLECTIONS = ("", "s", "es", "ed", "ing", "d")
_Y_INFLECTIONS = ("y", "ies", "ied", "ier", "iest", "ying")


def _normalize(text: str) -> str:
    """Lowercase with whitespace runs collapsed, so a multi-word keyword matches across line breaks."""
    return " ".join((text or "").lower().split())


def _contains(lowered: str, phrase: str, endings: tuple[str, ...] = ("",), prefix: bool = False) -> bool:
    """True when `phrase` (plus one of `endings`, or any word tail when `prefix`) appears in `lowered`
    as a whole word: no letter or digit directly before it or after the match.

    Plain string scanning on purpose: keywords include people's free-text off-limits terms and their
    names, and a regex built from them would outlive the session in a process-wide cache (`lru_cache`
    or the `re` module's own pattern cache) after the session is wiped (PRD F20, 15.1).
    """
    if not phrase:
        return False
    start = 0
    while (i := lowered.find(phrase, start)) != -1:
        start = i + 1
        if i > 0 and lowered[i - 1] in _ALNUM:
            continue
        end = i + len(phrase)
        if prefix:
            return True  # the word tail runs to the end of the word, so the right edge always holds
        for ending in endings:
            if lowered.startswith(ending, end) and (end + len(ending) >= len(lowered)
                                                    or lowered[end + len(ending)] not in _ALNUM):
                return True
    return False


def _keyword_in(lowered: str, keyword: str) -> bool:
    kw = _normalize(keyword)
    if kw.endswith(PREFIX_MARK):
        return _contains(lowered, kw[: -len(PREFIX_MARK)], prefix=True)
    if kw.endswith("y") and len(kw) > 3 and " " not in kw:
        return _contains(lowered, kw[:-1], _Y_INFLECTIONS)
    return _contains(lowered, kw, _INFLECTIONS)


def find_keyword(text: str, keywords: list[str] | tuple[str, ...]) -> str | None:
    """Return the first keyword found in `text` (word-boundary, case-insensitive), else None."""
    if not text:
        return None
    lowered = _normalize(text)
    for keyword in keywords:
        if keyword and _keyword_in(lowered, keyword):
            return keyword
    return None


def expand_off_limits(terms: list[str]) -> list[str]:
    """Expand off-limits entries (toggles and free text) into a flat keyword list."""
    keywords: list[str] = []
    for raw in terms:
        for term in re.split(r"[,;\n]", raw or ""):
            term = term.strip().lower()
            if len(term) < 2:
                continue
            keywords.append(term)
            toggle = OFF_LIMITS_TOGGLES.get(term) or OFF_LIMITS_TOGGLES.get(term.replace("_", " "))
            if toggle:
                keywords.extend(toggle)
            elif re.fullmatch(r"[a-z][a-z'\-]{3,}", term):
                keywords.append(term + PREFIX_MARK)
    return list(dict.fromkeys(keywords))


def off_limits_keywords(profiles: dict[int, Profile]) -> list[str]:
    terms: list[str] = []
    for profile in profiles.values():
        terms.extend(profile.off_limits)
    return expand_off_limits(terms)


def is_sensitive(text: str) -> bool:
    return find_keyword(text, SENSITIVE_KEYWORDS) is not None or bool(_INJURY_RE.search(text or ""))


def is_unsafe(text: str) -> bool:
    return find_keyword(text, UNSAFE_KEYWORDS) is not None


def name_present(text: str, names: dict[int, str]) -> bool:
    """True when any participant's name (full or first token) appears in `text`."""
    lowered = _normalize(text)
    for name in names.values():
        name = _normalize(name)
        if not name:
            continue
        if _contains(lowered, name) or _contains(lowered, name.split()[0]):
            return True
    return False


def validate_crumb_text(
    text: str,
    *,
    names: dict[int, str],
    profiles: dict[int, Profile],
    max_words: int = 28,
    require_name: bool = True,
    placeholders: list[str] | tuple[str, ...] = (),
) -> tuple[bool, str, list[str]]:
    """Check one spoken line (crumb text or overlap opener). Returns (ok, cleaned_text, errors).

    `require_name=False` when the people never typed a name ("Just talk"); `placeholders` are the labels
    used for them instead ("Person 1"), which must never be spoken."""
    cleaned = clean_text(text)
    errors: list[str] = []
    if not cleaned:
        return False, cleaned, ["empty text"]
    if word_count(cleaned) > max_words:
        errors.append(f"too many words ({word_count(cleaned)} > {max_words})")
    if require_name and not name_present(cleaned, names):
        errors.append("no participant name")
    if any(re.search(rf"(?<!\w){re.escape(p)}(?!\w)", cleaned, re.IGNORECASE) for p in placeholders if p):
        errors.append("placeholder name")
    hit = find_keyword(cleaned, off_limits_keywords(profiles))
    if hit:
        errors.append(f"off-limits term: {hit}")
    unsafe = find_keyword(cleaned, UNSAFE_KEYWORDS)
    if unsafe:
        errors.append(f"unsafe content: {unsafe}")
    return not errors, cleaned, errors


def short_place(location: str) -> str:
    """'Austin, Texas (HackTX at UT Austin)' -> 'Austin'."""
    head = re.split(r"[,(]", location or "", maxsplit=1)[0].strip()
    return head or "here"
