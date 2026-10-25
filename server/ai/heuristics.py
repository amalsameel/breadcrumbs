"""Deterministic text heuristics behind the mock paths (no network, no randomness).

Mock profile extraction rewrites spoken clauses into short third-person items, mock overlaps match
concept families, and mock crumbs/trails reuse the same stems and topic phrases.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

STOPWORDS = frozenset(
    """a about above after again all also am an and any are as at be been being but by can could
    did do does doing down for from had has have having he her here hers him his how i i'm i've i'd
    if in into is it it's its just like me more most my no not now of off on once only or other our
    out over own really same she so some such than that that's the their them then there these they
    this those through to too under until up very was we were what when where which while who why
    will with would you your yours yeah um uh okay ok lot lots thing things stuff kind sort pretty
    always never every something someone anything everything maybe actually definitely probably
    right now got get gets want wants love loves like likes enjoy enjoys one two three much many
    being doing going been make made way time times day days week weeks year years people person
    favorite talk tell said say says think know mean guess honestly basically""".split()
)

_JUNK_CLAUSES = re.compile(
    r"^(?:that'?s (?:it|about it|all)|not sure|i don'?t know|no idea|nothing|good question|"
    r"let me think|hmm+|um+|yeah|yes|no|okay|ok|sure|i guess|you know)\b",
    re.IGNORECASE,
)
_FILLER_PREFIX = re.compile(
    r"^(?:(?:um+|uh+|so|well|like|okay|ok|yeah|yes|oh|honestly|basically|i mean|i guess|i think|"
    r"you know|actually|oh my god|omg|and|but|also|then|right|hmm+)\b[\s,]*)+",
    re.IGNORECASE,
)
_INNER_FILLER = re.compile(r"\b(?:um+|uh+|you know|i mean)\b,?\s*", re.IGNORECASE)
_REASON_TAIL = re.compile(r"\s+(?:because|since|cause|'cause)\b.*$", re.IGNORECASE)
_FILLER_SUFFIX = re.compile(
    r"(?:[,\s]+(?:honestly|definitely|for sure|i guess|i think|probably|basically|really|actually|"
    r"lol|haha|you know|or something|and stuff|i suppose))+$",
    re.IGNORECASE,
)

# --- Stems and content words --------------------------------------------------------------

_SUFFIXES = ("ing", "ers", "er", "es", "s", "ed", "e")


def stem(word: str) -> str:
    w = word.lower().strip("'")
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]
            break
    if len(w) >= 4 and w[-1] == w[-2]:
        w = w[:-1]
    return w


def content_words(text: str) -> list[str]:
    words = re.findall(r"[a-z][a-z'\-]+", text.lower())
    return [w for w in words if len(w) >= 3 and w not in STOPWORDS]


def stems(text: str) -> set[str]:
    return {stem(w) for w in content_words(text)}


_DANGLING = {"in", "a", "an", "the", "to", "of", "and", "or", "with", "for", "at", "on", "my", "their",
             "your", "how", "that", "who", "from", "by", "but", "so"}


def short(text: str, max_words: int) -> str:
    """First `max_words` words, without a dangling preposition or article at the end."""
    words = text.split()[:max_words]
    while len(words) > 1 and words[-1].lower().strip(",;:.") in _DANGLING:
        words.pop()
    return " ".join(words).rstrip(" ,;:.")


# --- Concept families (mock overlaps) -------------------------------------------------------

FAMILIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "movement": ("staying active", (
        "running", "run", "runner", "marathon", "half marathon", "5k", "climbing", "bouldering",
        "hiking", "hike", "cycling", "biking", "bike", "swimming", "yoga", "gym", "lifting",
        "soccer", "basketball", "tennis", "volleyball", "skiing", "snowboarding", "surfing",
        "dance", "dancing", "skateboarding", "pickleball", "workout", "training",
    )),
    "food": ("food", (
        "cooking", "cook", "baking", "bake", "food", "recipe", "restaurant", "coffee", "tacos",
        "taco", "bbq", "barbecue", "ramen", "sushi", "pizza", "chef", "kitchen", "brunch", "tea",
        "breakfast", "dinner", "snacks", "dessert", "bread", "sourdough", "foodie",
    )),
    "music": ("music", (
        "music", "guitar", "piano", "concert", "concerts", "band", "singing", "sing", "song",
        "songs", "playlist", "festival", "vinyl", "drums", "dj", "jazz", "violin", "choir",
    )),
    "travel": ("travel", (
        "travel", "traveling", "trip", "trips", "japan", "tokyo", "abroad", "backpacking",
        "road trip", "flight", "europe", "mexico", "vacation", "study abroad", "country",
    )),
    "tech": ("building things", (
        "coding", "code", "programming", "software", "app", "apps", "startup", "hackathon",
        "engineering", "computer", "robot", "robotics", "data", "product", "design", "ai",
        "machine learning", "website",
    )),
    "games": ("games", (
        "gaming", "games", "game", "video games", "board games", "chess", "nintendo", "puzzles",
        "puzzle", "dungeons", "poker", "crossword",
    )),
    "arts": ("creative stuff", (
        "art", "painting", "drawing", "photography", "photos", "film", "movies", "movie",
        "writing", "poetry", "books", "reading", "novel", "pottery", "theater", "anime",
        "comics", "knitting", "crafts",
    )),
    "outdoors": ("being outdoors", (
        "camping", "nature", "beach", "lake", "park", "parks", "gardening", "plants", "birds",
        "bats", "stars", "mountains", "trail", "trails", "kayaking", "fishing",
    )),
    "animals": ("animals", ("dog", "dogs", "cat", "cats", "puppy", "pet", "pets", "horse", "horses")),
    "learning": ("learning new things", (
        "learning", "language", "languages", "japanese", "spanish", "french", "class", "studying",
        "research", "lab", "podcast", "podcasts", "history", "science",
    )),
}

# (name, side A keywords, side B keywords, label A, label B)
CONTRAST_PAIRS: tuple[tuple[str, tuple[str, ...], tuple[str, ...], str, str], ...] = (
    ("schedule", ("morning", "mornings", "early", "sunrise", "dawn", "early bird"),
     ("night", "nights", "late", "midnight", "night owl"), "a morning person", "a night owl"),
    ("planning", ("plan", "planned", "planner", "organized", "spreadsheet", "itinerary", "schedule"),
     ("spontaneous", "random", "impulsive", "last minute", "improvise"), "a planner", "spontaneous"),
    ("pace", ("cozy", "couch", "homebody", "staying in", "netflix", "quiet"),
     ("outdoors", "adventure", "camping", "hiking", "mountains", "road trip"), "a cozy homebody",
     "an adventurer"),
    ("place", ("city", "downtown", "urban", "skyline"), ("nature", "countryside", "farm", "woods", "ranch"),
     "a city person", "a nature person"),
)


def _matches(text: str, keyword: str) -> bool:
    if " " in keyword:
        return keyword in text.lower()
    return stem(keyword) in stems(text) or keyword in re.findall(r"[a-z0-9]+", text.lower())


_COMMON_WORDS = (STOPWORDS | {kw for _, kws in FAMILIES.values() for kw in kws if " " not in kw} | {
    "rock", "story", "honestly", "making", "playing", "watching", "learning", "building", "reading"}) - {
    "japan", "tokyo", "europe", "mexico", "nintendo", "spanish", "french", "japanese", "ai", "dj"}


def families_of(text: str) -> set[str]:
    return {name for name, (_, kws) in FAMILIES.items() if any(_matches(text, kw) for kw in kws)}


def family_label(name: str) -> str:
    return FAMILIES[name][0]


def contrast_side(text: str, pair_index: int) -> str | None:
    """'a' or 'b' when `text` matches one side of CONTRAST_PAIRS[pair_index]."""
    _, side_a, side_b, _, _ = CONTRAST_PAIRS[pair_index]
    a = any(_matches(text, kw) for kw in side_a)
    b = any(_matches(text, kw) for kw in side_b)
    if a == b:
        return None
    return "a" if a else "b"


# --- Pronouns and verbs ----------------------------------------------------------------------

_THIRD = (
    (r"\bI am\b", "they are"), (r"\bI'm\b", "they're"), (r"\bI've\b", "they've"), (r"\bI'd\b", "they'd"),
    (r"\bI'll\b", "they'll"), (r"\bI was\b", "they were"), (r"\bI\b", "they"), (r"\bmyself\b", "themselves"),
    (r"\bmy\b", "their"), (r"\bmine\b", "theirs"), (r"\bme\b", "them"),
)
_SECOND = (
    (r"\bI am\b", "you are"), (r"\bI'm\b", "you're"), (r"\bI've\b", "you've"), (r"\bI'd\b", "you'd"),
    (r"\bI'll\b", "you'll"), (r"\bI was\b", "you were"), (r"\bI\b", "you"), (r"\bmyself\b", "yourself"),
    (r"\bmy\b", "your"), (r"\bmine\b", "yours"), (r"\bme\b", "you"),
)


def _swap(text: str, table: tuple[tuple[str, str], ...]) -> str:
    for pattern, repl in table:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    return text


def to_third(text: str) -> str:
    return _swap(text, _THIRD)


def to_second(text: str) -> str:
    return _swap(text, _SECOND)


IRREGULAR_PAST = {
    "grew": "grow", "went": "go", "made": "make", "ran": "run", "saw": "see", "got": "get",
    "had": "have", "took": "take", "came": "come", "wrote": "write", "built": "build",
    "began": "begin", "found": "find", "lost": "lose", "spent": "spend", "taught": "teach",
    "left": "leave", "met": "meet", "did": "do", "was": "be", "were": "be", "won": "win",
    "swam": "swim", "drove": "drive", "flew": "fly", "ate": "eat", "fell": "fall", "felt": "feel",
    "knew": "know", "thought": "think", "bought": "buy", "brought": "bring", "sang": "sing",
    "gave": "give", "became": "become", "broke": "break", "chose": "choose", "sold": "sell",
    "told": "tell", "held": "hold", "kept": "keep", "slept": "sleep", "said": "say", "paid": "pay",
    "heard": "hear", "sat": "sit", "stood": "stand", "wore": "wear", "rode": "ride",
}
_DOUBLE_FINAL = {"run", "swim", "plan", "shop", "sit", "get", "put", "win", "stop", "begin", "jog",
                 "hit", "cut", "set", "dig", "chat", "skip", "drum", "knit", "spin"}
_MODALS = {"can", "could", "will", "would", "should", "might", "must", "may", "used"}


def is_past(verb: str) -> bool:
    v = verb.lower()
    return v in IRREGULAR_PAST or (v.endswith("ed") and len(v) > 3)


def to_gerund(verb: str) -> str:
    v = verb.lower()
    if v.endswith("ing"):
        return v
    if v in IRREGULAR_PAST:
        v = IRREGULAR_PAST[v]
    elif v.endswith("ied"):
        return v[:-3] + "ying"
    elif v.endswith("ed") and len(v) > 4:
        return v[:-2] + "ing"
    if v == "be":
        return "being"
    if v.endswith("ie"):
        return v[:-2] + "ying"
    if v.endswith("e") and not v.endswith(("ee", "ye", "oe")) and len(v) > 2:
        return v[:-1] + "ing"
    if v in _DOUBLE_FINAL:
        return v + v[-1] + "ing"
    return v + "ing"


def conjugate_third(verb: str) -> str:
    v = verb.lower()
    irregular = {"have": "has", "do": "does", "go": "goes", "be": "is", "am": "is"}
    if v in irregular:
        return irregular[v]
    if is_past(v) or v in _MODALS:
        return v
    if v.endswith(("s", "sh", "ch", "x", "z", "o")):
        return v + "es"
    if v.endswith("y") and len(v) > 1 and v[-2] not in "aeiou":
        return v[:-1] + "ies"
    return v + "s"


# --- Clause splitting and rewriting (mock profile extraction) ---------------------------------


def split_clauses(answer: str) -> list[str]:
    """Split one spoken answer into candidate item clauses."""
    text = _INNER_FILLER.sub(" ", answer or "")
    sentences = re.split(r"(?<=[.!?])\s+|\s*;\s*|\s+-\s+", text)
    clauses: list[str] = []
    for sentence in sentences:
        parts = re.split(
            r",?\s+(?:and|but|plus|so|also|though|and also)\s+(?=(?:i|i'm|i've|i'd|my|we)\b)"
            r"|,\s+(?=(?:i|i'm|i've|i'd|my)\b)",
            sentence,
            flags=re.IGNORECASE,
        )
        for part in parts:
            part = _FILLER_PREFIX.sub("", part.strip())
            part = _REASON_TAIL.sub("", part)
            part = _FILLER_SUFFIX.sub("", re.sub(r"[\s,.!?;:]+$", "", part))
            part = re.sub(r"[\s,.!?;:]+$", "", part).strip()
            part = re.sub(r"\s+(?:and|but|so|or)$", "", part, flags=re.IGNORECASE)
            if part and not _JUNK_CLAUSES.match(part) and content_words(part):
                clauses.append(_lower_common_initial(part))
    return clauses


def _lower_common_initial(clause: str) -> str:
    """'Rock climbing' -> 'rock climbing', but keep 'Tokyo' and 'I'."""
    first = clause.split(" ", 1)[0]
    if first != "I" and first[:1].isupper() and (len(first) == 1 or first[1:].islower()) and first.lower() in _COMMON_WORDS:
        return first.lower() + clause[len(first):]
    return clause


@dataclass
class Rewrite:
    text: str  # third-person item phrase, e.g. "loves rock climbing"
    core: str  # second-person topic phrase for hooks, e.g. "rock climbing"
    kind: str | None = None  # kind implied by the sentence pattern, if any
    hook_style: str | None = None  # "plan" for future goals


def rewrite_clause(clause: str) -> Rewrite:
    c = clause.strip(" ,.!?")

    m = re.match(r"^people (?:are|get) (?:always |usually |often )?surprised (?:to (?:learn|hear|find out) )?(?:that )?i\s+(.+)", c, re.I)
    if m:
        inner = rewrite_clause(f"I {m.group(1)}")
        return Rewrite(inner.text, inner.core, "fun_fact")

    m = re.match(r"^(?:a |my |the )?perfect (saturday|sunday|day|weekend)(?: for me)?\s+(?:is|would be|looks like|'s)\s+(.+)", c, re.I)
    if m:
        day, rest = m.group(1).lower(), m.group(2)
        day = day.capitalize() if day in ("saturday", "sunday") else day
        return Rewrite(f"perfect {day}: {to_third(rest)}", to_second(rest), "preference", "best_part")

    m = re.match(r"^(.+?)\s+makes? (?:me|them) (?:so |really |unreasonably |instantly |super )*happy\b", c, re.I)
    if m and not m.group(1).lower().startswith(("i ", "it ")):
        thing = _lower_common_initial(m.group(1))
        return Rewrite(f"happy about {to_third(thing)}", to_second(thing), "preference", "happy")

    m = re.search(r"\b(?:story|stories)\b(?:\s+(?:about|of))?(?:\s+(?:the|that)\s+time)?(?:\s+(?:when|that))?\s+(i\s+|we\s+)?(.+)", c, re.I) \
        or re.search(r"\bthe time\s+(?:when\s+|that\s+)?(i\s+|we\s+)?(.+)", c, re.I)
    if m:
        rest = m.group(2)
        core = f"the time you {to_second(rest)}" if m.group(1) else to_second(rest)
        return Rewrite(f"story: {to_third(rest)}", core, "story")

    m = re.match(r"^i\s+could\s+talk\s+(?:for\s+(?:an\s+)?hours?\s+)?about\s+(.+?)(?:\s+for\s+(?:an\s+)?hours?)?$", c, re.I)
    if m:
        return Rewrite(f"could talk for hours about {to_third(m.group(1))}", to_second(m.group(1)), "interest")

    m = re.match(r"^(?:i(?:'m|\s+am)\s+)?(?:really\s+|super\s+|kind of\s+|pretty\s+|very\s+)?"
                 r"(?:into|obsessed with|passionate about|a (?:big|huge) fan of|crazy about)\s+(.+)", c, re.I)
    if m:
        return Rewrite(f"into {to_third(m.group(1))}", to_second(m.group(1)))

    m = re.match(r"^i\s+(?:really\s+|also\s+|just\s+|absolutely\s+|kind of\s+)?(love|like|enjoy|adore)\s+(.+)", c, re.I)
    if m:
        verb, rest = m.group(1).lower(), m.group(2)
        if rest.lower().startswith("when "):
            return Rewrite(f"{verb}s it {to_third(rest)}", f"those moments {to_second(rest)}", "preference")
        return Rewrite(f"{verb}s {to_third(rest)}", to_second(rest))

    m = re.match(r"^i(?:'m|\s+am)\s+(?:really\s+|so\s+)?looking forward to\s+(.+)", c, re.I)
    if m:
        return Rewrite(f"looking forward to {to_third(m.group(1))}", to_second(m.group(1)), "goal", "excited")

    m = re.match(r"^i(?:'m|\s+am)?\s+(?:looking|hoping|trying|want|hope|would like|'d like)\s+to\s+meet\s+(.+)", c, re.I)
    if m:
        return Rewrite(f"hoping to meet {to_third(m.group(1))}", f"meeting {to_second(m.group(1))}", "seeking")

    m = re.match(r"^i(?:'m|\s+am)\s+(?:also\s+)?looking for\s+(.+)", c, re.I)
    if m:
        return Rewrite(f"looking for {to_third(m.group(1))}", f"finding {to_second(m.group(1))}", "seeking")

    m = re.match(r"^i\s+(?:can|could)\s+(?:definitely\s+|probably\s+)?help\s+(?:people\s+|someone\s+|others\s+|folks\s+)?(?:with\s+)?(.+)", c, re.I)
    if m:
        return Rewrite(f"can help with {to_third(m.group(1))}", to_second(m.group(1)), "offer")

    m = re.match(r"^i(?:'m|\s+am)\s+(?:really\s+|pretty\s+)?good at\s+(.+)", c, re.I)
    if m:
        return Rewrite(f"good at {to_third(m.group(1))}", to_second(m.group(1)), "offer")

    m = re.match(r"^i(?:'m|\s+am)?\s+(?:really\s+)?(want|hope|hoping|plan|planning|would love|'d love|wanna|trying)\s+to\s+([a-z]+)(.*)", c, re.I)
    if m:
        lead = "trying to" if m.group(1).lower() == "trying" else "wants to"
        verb, rest = m.group(2), m.group(3)
        return Rewrite(f"{lead} {verb}{to_third(rest)}", f"{to_gerund(verb)}{to_second(rest)}", "goal", "plan")

    m = re.match(r"^i(?:'m|\s+am|'ve been|\s+have been)\s+(?:currently\s+|really\s+|also\s+|just\s+)?([a-z]+ing)\b(.*)", c, re.I)
    if m:
        return Rewrite(f"{m.group(1)}{to_third(m.group(2))}", f"{m.group(1)}{to_second(m.group(2))}")

    m = re.match(r"^i(?:'m|\s+am)\s+(.+)", c, re.I)
    if m:
        return Rewrite(to_third(m.group(1)), f"being {to_second(m.group(1))}")

    m = re.match(r"^i\s+(?:can|used to)\s+([a-z]+)(.*)", c, re.I)
    if m:
        lead = "used to" if "used to" in c.lower()[:12] else "can"
        return Rewrite(f"{lead} {m.group(1)}{to_third(m.group(2))}", f"{to_gerund(m.group(1))}{to_second(m.group(2))}", "fun_fact")

    m = re.match(r"^i\s+(?:also\s+|really\s+|just\s+|actually\s+)?([a-z]+)\b(.*)", c, re.I)
    if m and m.group(1).lower() not in _MODALS:
        verb, rest = m.group(1), m.group(2)
        return Rewrite(f"{conjugate_third(verb)}{to_third(rest)}", f"{to_gerund(verb)}{to_second(rest)}")

    m = re.match(r"^my\s+(.+)", c, re.I)
    if m:
        return Rewrite(f"their {to_third(m.group(1))}", f"your {to_second(m.group(1))}")

    return Rewrite(to_third(c), to_second(c))


KIND_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("story", ("story", "the time", "got lost", "remember when", "once i")),
    ("seeking", ("looking for", "hoping to meet", "want to meet", "meet people", "cofounder", "co-founder", "mentor")),
    ("offer", ("help with", "can help", "could help", "good at", "teach", "advice on")),
    ("goal", ("want to", "hoping to", "plan to", "planning", "training for", "trying to", "learning",
              "goal", "someday", "looking forward", "signed up", "working toward", "working on")),
    ("value", ("care about", "believe in", "important to me", "matters to me")),
    ("fun_fact", ("surprised", "surprising", "fun fact", "secretly", "believe it or not", "used to", "i can")),
    ("preference", ("perfect saturday", "favorite", "prefer", "makes me happy", "unreasonably happy",
                    "love when", "best part", "go-to")),
)

# Default kind per interview question index (PRD 6.3), used when no keyword matches.
QUESTION_KINDS: dict[str, tuple[str, str, str]] = {
    "social": ("interest", "story", "goal"),
    "networking": ("interest", "goal", "offer"),
    "speed_dating": ("preference", "preference", "fun_fact"),
}
KIND_DEPTH = {"interest": 1, "fun_fact": 1, "preference": 2, "story": 2, "offer": 2, "seeking": 2,
              "goal": 3, "value": 3}
HOOK_TEMPLATES = {
    "interest": "What do you love most about {core}?",
    "story": "What's the story with {core}?",
    "goal": "How is {core} going so far?",
    "fun_fact": "What's the story behind {core}?",
    "value": "Why does {core} matter so much to you?",
    "preference": "What makes {core} so good?",
    "offer": "How did you get good at {core}?",
    "seeking": "What are you hoping to get from {core}?",
}


def guess_kind(clause: str, default: str) -> str:
    low = clause.lower()
    for kind, keywords in KIND_KEYWORDS:
        if any(kw in low for kw in keywords):
            return kind
    return default


def make_hook(kind: str, core: str, hook_style: str | None = None, max_words: int = 18) -> str:
    if kind == "story" and core.lower().startswith("the time"):
        template = "What happened {core}?"
    elif kind == "goal" and hook_style == "plan":
        template = "What's the plan for {core}?"
    elif hook_style == "excited":
        template = "What are you most excited about for {core}?"
    elif hook_style == "best_part":
        template = "What's the best part of {core}?"
    elif hook_style == "happy":
        template = "What is it about {core} that makes you happy?"
    else:
        template = HOOK_TEMPLATES.get(kind, HOOK_TEMPLATES["interest"])
    overhead = len(template.replace("{core}", "").split())
    trimmed = short(core, max(2, max_words - overhead))
    return template.format(core=trimmed)


_TOPIC_PREFIX = re.compile(
    r"^(?:loves|likes|enjoys|adores|into|good at|can help with|could talk for hours about|looking forward to|looking for|hoping to meet|happy about|perfect \w+:|"
    r"wants to|trying to)\s+",
    re.IGNORECASE,
)


def topic_of(item_text: str) -> str:
    """Noun-ish topic phrase from an item, e.g. 'loves rock climbing' -> 'rock climbing'."""
    text = item_text.strip()
    if text.lower().startswith("story:"):
        rest = text[6:].strip()
        first = rest.split(" ", 1)[0].lower() if rest else ""
        return f"the time they {rest}" if first and is_past(first) else rest
    stripped = _TOPIC_PREFIX.sub("", text)
    if stripped != text:
        first = stripped.split(" ", 1)[0]
        if text.lower().startswith(("wants to", "trying to")) and first:
            return to_gerund(first) + stripped[len(first):]
        return stripped
    words = text.split()
    first, rest = words[0].lower(), text[len(words[0]):]
    if first.endswith("ing") or first in {"a", "an", "the", "their"}:
        return text
    if first in _THIRD_IRREGULAR:
        return _THIRD_IRREGULAR[first] + rest
    if is_past(first):
        return to_gerund(first) + rest
    # Treat a leading "-s" word as a verb only when an object follows ("plays guitar", not "dogs and cats").
    if len(words) >= 2 and words[1].lower() != "and" and first.endswith("s") and not first.endswith("ss"):
        if first.endswith("ies"):
            verb = first[:-3] + "y"
        elif first.endswith(("ches", "shes", "xes", "oes")):
            verb = first[:-2]
        else:
            verb = first[:-1]
        return to_gerund(verb) + rest
    return text


_THIRD_IRREGULAR = {"has": "having", "does": "doing", "goes": "going", "is": "being"}


# --- Transcript helpers ----------------------------------------------------------------------


def transcript_lines(transcript: str) -> list[tuple[str, str]]:
    """'Maya: hi\\nDev: hey' -> [('Maya', 'hi'), ('Dev', 'hey')]."""
    lines: list[tuple[str, str]] = []
    for raw in (transcript or "").splitlines():
        if ":" not in raw:
            continue
        speaker, text = raw.split(":", 1)
        speaker, text = speaker.strip(), text.strip()
        if speaker and text:
            lines.append((speaker, text))
    return lines


def quote_snippet(text: str, max_words: int = 6) -> str:
    """A few words from the last clause of a turn, for callbacks."""
    clauses = [c for c in re.split(r"[.!?;,]+", text) if content_words(c)]
    if not clauses:
        return ""
    clause = _FILLER_PREFIX.sub("", clauses[-1].strip())
    return short(clause, max_words)
