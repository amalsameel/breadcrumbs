"""Hold lexicon (PRD 8.6, Appendix A) and safety lexicon (PRD 15.3)."""
import pytest

from config import Params
from engine.hold import (
    HOLD_PATTERNS,
    NEGATIVE_PATTERNS,
    is_ambiguous_fragment,
    is_distress,
    match_hold,
    start_hold,
    tail,
)


@pytest.mark.parametrize(
    "text, kind",
    [
        # Appendix A test positives
        ("Wait, let me show you.", "show"),
        ("hold on, where is it", "show"),
        ("Hmm, good question.", "think"),
        ("Let me pull it up real quick.", "show"),
        ("sorry I have to take this.", "external"),
        # one per pattern family
        ("Oh I have it on my phone, let me find it", "show"),
        ("Let me grab my phone", "show"),
        ("You have to look at this", "show"),
        ("Check this out", "show"),
        ("Give me a second", "show"),
        ("just a sec", "show"),
        ("I'm trying to find the photo", "show"),
        ("I am looking", "show"),
        ("Where was that", "show"),
        ("It was so good, wait", "show"),
        ("Let me think", "think"),
        ("Ummm", "think"),
        ("That's a tough one.", "think"),
        ("thats a hard one", "think"),
        ("How do I put this", "think"),
        ("Great question!", "think"),
        ("Sorry, I need to take this", "external"),
        ("I got to take this", "external"),
        ("Oh, excuse me", "external"),
        ("brb", "external"),
        ("I'll be right back", "external"),
    ],
)
def test_positives(text: str, kind: str) -> None:
    assert match_hold(text) == kind


@pytest.mark.parametrize(
    "text",
    [
        # Appendix A test negatives
        "Wait, really? That's insane, how did you even get in?",
        "I can't wait for the trip.",
        "Show me sometime!",
        # more negatives
        "Wait, seriously? You did that on purpose and nobody noticed at all?",
        "I honestly cant wait",
        "We waited in line for an hour.",
        "That was a great time at the lake with everyone.",
        "",
    ],
)
def test_negatives(text: str) -> None:
    assert match_hold(text) is None


def test_case_insensitive_and_curly_apostrophes() -> None:
    assert match_hold("LET ME SHOW YOU") == "show"
    assert match_hold("That’s a tough one") == "think"
    assert match_hold("I can’t wait") is None


def test_tail_only_rule() -> None:
    """The phrase must be in the last 8 words: a hold phrase followed by more speech is not a hold."""
    early = "Hold on, so then we drove all the way to the coast and camped there."
    assert match_hold(early) is None
    late = "So then we drove to the coast and, hold on"
    assert match_hold(late) == "show"
    # exactly at the 8-word boundary
    assert match_hold("one two three four five six let me think") == "think"
    assert match_hold("let me think one two three four five six seven eight") is None


def test_tail_window_is_configurable() -> None:
    text = "let me think about that for a little bit more"
    assert match_hold(text, n_words=8) is None
    assert match_hold(text, n_words=12) == "think"
    assert tail("a b c d e f g h i j", 3) == "h i j"
    assert tail("anything", 0) == ""


def test_wait_only_as_last_word() -> None:
    assert match_hold("wait") == "show"
    assert match_hold("Oh, wait...") == "show"
    assert match_hold("wait for it, the best part is coming") is None


def test_latest_phrase_wins_when_kinds_overlap() -> None:
    assert match_hold("hmm good question, hold on") == "show"
    assert match_hold("hold on, let me think") == "think"


def test_patterns_match_appendix_a_verbatim() -> None:
    assert len(HOLD_PATTERNS["show"]) == 6
    assert len(HOLD_PATTERNS["think"]) == 5
    assert len(HOLD_PATTERNS["external"]) == 3
    assert NEGATIVE_PATTERNS[1] == r"\bcan'?t wait\b"
    assert HOLD_PATTERNS["show"][5] == r"\bwait\W*$"


@pytest.mark.parametrize(
    "text, ambiguous",
    [
        ("and then we", True),  # no ending punctuation
        ("how did...", True),  # trailing off
        ("No way, where?", True),  # under 4 words
        ("Yeah.", True),
        ("That is a really good point.", False),
        ("We drove all the way there!", False),
        ("", False),
    ],
)
def test_ambiguous_fragment(text: str, ambiguous: bool) -> None:
    assert is_ambiguous_fragment(text) is ambiguous


@pytest.mark.parametrize(
    "text, distress",
    [
        ("Honestly some days I want to die", True),
        ("I think I'm having a panic attack", True),
        ("Someone call 911", True),
        ("He keeps hurting me", True),
        ("I am not safe at home", True),
        ("That movie was killer", False),
        ("I'm dying to try that taco place", False),
        ("We should call the restaurant", False),
    ],
)
def test_distress_lexicon(text: str, distress: bool) -> None:
    assert is_distress(text) is distress


def test_hold_record_durations() -> None:
    p = Params()
    show = start_hold("show", 100.0, "lexical", p, turn_id=3)
    assert show.until == 160.0 and show.suppresses and show.turn_id == 3
    think = start_hold("think", 100.0, "lexical", p)
    assert think.until == 120.0 and not think.suppresses
    ext = start_hold("external", 100.0, "llm", p)
    assert ext.until == 190.0 and ext.suppresses
    assert show.remaining(150.0) == 10.0 and not show.expired(159.9) and show.expired(160.0)
    assert show.to_snapshot(150.0) == {"kind": "show", "remaining_s": 10.0, "source": "lexical"}
    manual = start_hold("show", 0.0, "manual", p, duration_s=60)
    assert manual.until == 60.0
