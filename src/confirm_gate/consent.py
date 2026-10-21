"""What a user's confirmation words decide.

The gate asks one narrow question of a transcript: did the user pick one of the
options on screen, say yes to the final card, say no, or none of those? Anything
that is not clearly one answer is ``unclear``. Unclear costs the user one more
try; a wrong accept costs them the wrong door unlocked. So every ambiguous case
here resolves to unclear.

Rules, each one there because the alternative produced a false accept:

* A bare "do" is never two. In Hindi "do" is both *two* and *give/do it*
  ("isko kar do" is *please do this*), and in English it is a verb. It counts
  as two only next to "number" ("do number", "number do").
* "one" after an ordinal or a demonstrative is a pronoun: "the second one" is
  option 2, not options 2 and 1. "that one" picks nothing.
* Yes and no in one utterance, or two different numbers, is unclear.
* A number on the final yes/no card is unclear, not yes.
* Plain "yes" cannot choose among several options.
* More than two words outside the confirmation vocabulary means the user was
  saying something else ("order two of the blue cables now").
* No sound-alikes: "to" is not "two". A mishearing is refused, not repaired.
* Hesitation, questions and qualifiers make it unclear: "ok, hang on", "okay, who is it?", "yes but",
  "haan, kaun hai". A person still deciding has not decided. (A trailing "?" alone is not a question:
  recognisers add one to a rising "yes".)
* A Devanagari word with no mapping counts as an unknown word instead of disappearing, so a long Hindi
  sentence that merely contains "haan" is not a yes.
* "Second" after "a", "ek", "one", "just", "half" or "in" is time, not option 2: "ek second" means
  *one moment*. Hedges ("maybe", "probably", "shayad", "sochne do", "ruk", "kal") are undecided.
* On the final (high-risk) card no unknown word is allowed at all, except the card's own words
  ("yes, unlock the front door"): an unmapped word could be a hedge in a language not mapped here.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

Kind = Literal["choose", "yes", "no", "unclear"]
Stage = Literal["choose", "final"]

# Consent vocabulary in Devanagari, mapped to the Latin words below. Only these
# words are mapped; an unmapped Devanagari word is dropped, so a phrase that
# cannot be read confidently does not match.
DEVANAGARI = {
    "हाँ": "haan",
    "हां": "haan",
    "हा": "haan",
    "जी": "ji",
    "हाँजी": "haanji",
    "ठीक": "theek",
    "है": "hai",
    "नहीं": "nahin",
    "नही": "nahin",
    "ना": "na",
    "मत": "mat",
    "करो": "karo",
    "रुको": "ruko",
    "एक": "ek",
    "दो": "do",
    "तीन": "teen",
    "नंबर": "number",
    "नम्बर": "number",
    "पहला": "pehla",
    "पहली": "pehli",
    "दूसरा": "doosra",
    "दूसरी": "doosri",
    "दुसरा": "doosra",
    "तीसरा": "teesra",
    "तीसरी": "teesri",
    "वाला": "wala",
    "वाली": "wali",
    "यस": "yes",
    "येस": "yes",       # Transcribe hi-IN writes an English "yes" this way
    "त्वो": "two",       # ...and an English "two" this way. ("तू" is "you", never two.)
    "ओने": "one",
    "नो": "no",
    "वन": "one",
    "टू": "two",
    "थ्री": "three",
    "फर्स्ट": "first",
    "सेकंड": "second",
    "थर्ड": "third",
    "ओके": "okay",
    "कैंसल": "cancel",
    "लेकिन": "lekin",
    "मगर": "magar",
    "कौन": "kaun",
    "क्या": "kya",
    "क्यों": "kyun",
    "कब": "kab",
    "कहाँ": "kahan",
    "कैसे": "kaise",
    "बाद": "baad",
    "ज़रा": "zara",
    "जरा": "zara",
    "थोड़ा": "thoda",
    "अभी": "abhi",
    "मिनट": "minute",
    "सेकेंड": "second",
    "शायद": "shayad",
    "रुक": "ruk",
    "रुकिए": "rukiye",
    "रुकिये": "rukiye",
    "ठहरो": "thehro",
    "ठहरिए": "thehriye",
    "सोचने": "sochne",
    "सोचो": "socho",
    "सोचता": "sochta",
    "सोचती": "sochti",
    "देखते": "dekhte",
    "देखेंगे": "dekhenge",
    "कल": "kal",
    "बिल्कुल": "bilkul",
    "बिलकुल": "bilkul",
    "ज़रूर": "zaroor",
    "जरूर": "zaroor",
    "सही": "sahi",
    "रहने": "rehne",
    "छोड़ो": "chhodo",
    "छोड़िए": "chhodo",
    "सॉरी": "sorry",
    "बस": "bas",
}

_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_EDGE = "।॥.,!?;:\"'()-—… "

# Option words → option number. English cardinals, ordinals, digits and Hinglish
# ordinals stand alone. Hindi cardinals (ek, do, teen) need "number" next to them.
_STANDALONE = {
    "1": 1, "one": 1, "first": 1, "1st": 1, "pehla": 1, "pehli": 1, "pahla": 1, "pehle": 1,
    "2": 2, "two": 2, "second": 2, "2nd": 2, "doosra": 2, "doosri": 2, "dusra": 2, "dusri": 2, "doosre": 2,
    "3": 3, "three": 3, "third": 3, "3rd": 3, "teesra": 3, "teesri": 3, "tisra": 3, "tisri": 3,
    "4": 4, "four": 4, "fourth": 4, "4th": 4,
    "5": 5, "five": 5, "fifth": 5, "5th": 5,
    "0": 0, "zero": 0,
}
_NEEDS_NUMBER = {"ek": 1, "do": 2, "teen": 3}
_ORDINALS = {"first", "second", "third", "fourth", "fifth", "last", "1st", "2nd", "3rd", "4th", "5th",
             "pehla", "pehli", "doosra", "doosri", "dusra", "dusri", "teesra", "teesri", "tisra", "tisri"}
_DEMONSTRATIVES = {"that", "this", "the", "which", "same", "other", "wo", "woh", "yeh", "ye"}

_YES = {"yes", "yeah", "yea", "yep", "yup", "confirm", "confirmed", "okay", "ok", "sure",
        "haan", "han", "haa", "haanji", "theek", "thik"}
_NO = {"no", "nope", "nah", "cancel", "stop", "none", "neither", "nahin", "nahi", "nai", "na",
       "mat", "ruko", "dont", "don't", "not", "never", "wrong"}

# Any of these means the person has not decided yet.
_HESITATE = {"wait", "hold", "hang", "later", "minute", "minutes", "moment", "sec", "secs", "seconds", "bit", "soon",
             "tomorrow", "tonight", "baad", "zara", "thoda", "abhi", "kal", "ruk", "rukiye", "thehro", "thehriye",
             "thehra", "sochne", "socho", "sochta", "sochti", "sochenge", "dekhte", "dekhenge"}
_HEDGE = {"maybe", "perhaps", "probably", "possibly", "unsure", "actually", "think", "guess", "might", "kinda",
          "shayad", "shaayad", "nevermind", "never", "scratch", "suppose", "dunno", "sorry", "rehne", "rehene",
          "chhodo", "chodo", "chhod", "kidding", "oops"}
# "second" after these is time ("ek second", "two more seconds"), never option 2.
_TIME_BEFORE_SECOND = {"a", "ek", "one", "two", "do", "just", "half", "in", "few", "couple", "another", "more", "bas",
                       "some", "teen", "three"}
_QUESTION = {"who", "what", "why", "where", "when", "which", "how", "kaun", "kya", "kyun", "kyon", "kab", "kahan", "kaise"}
_QUALIFY = {"but", "lekin", "magar", "except", "unless", "par"}
_UNDECIDED = _HESITATE | _HEDGE | _QUESTION | _QUALIFY
_UNMAPPED = "\u2047"  # placeholder for a Devanagari word we cannot read: always an unknown word

# Words that may surround a confirmation without changing it.
_FILLER = {
    "number", "option", "card", "choice", "the", "a", "please", "pls", "i", "want", "pick", "choose",
    "select", "take", "go", "with", "it", "is", "its", "that's", "thats", "one", "this", "that",
    "um", "uh", "umm", "hmm", "er", "ah", "oh", "ji", "hai", "wala", "wali", "wale", "vala", "waala",
    "karo", "kar", "do", "say", "said", "of", "these", "those", "them", "all", "thank", "you", "thanks",
    "just", "now", "am", "mean", "meant", "like", "would", "id", "i'd", "yes", "no",
    "ahead", "correct", "absolutely", "definitely", "bilkul", "zaroor", "sahi",
}


@dataclass(frozen=True)
class Decision:
    kind: Kind
    option: int | None
    heard: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def words(text: str) -> list[str]:
    """The comparable words of *text*: one script, lower case, digits for numerals."""
    out: list[str] = []
    for raw in (text or "").translate(_DIGITS).split():
        token = raw.strip(_EDGE)
        mapped = DEVANAGARI.get(token)
        if mapped is not None:
            out.append(mapped)
            continue
        if any("\u0900" <= ch <= "\u097f" for ch in token):
            out.append(_UNMAPPED)  # an unreadable Hindi word is still a word, and an unknown one
            continue
        latin = re.sub(r"[^a-z0-9'\s]", " ", token.lower())
        out.extend(w.strip("'") for w in latin.split() if w.strip("'"))
    return out


def normalise(text: str) -> str:
    """*text* as one comparable string, with number words as digits."""
    cardinals = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5"}
    return " ".join(cardinals.get(w, w) for w in words(text))


def _option_mentions(ws: list[str], n_options: int) -> list[int]:
    found: list[int] = []
    for i, w in enumerate(ws):
        prev = ws[i - 1] if i > 0 else ""
        nxt = ws[i + 1] if i + 1 < len(ws) else ""
        if w == "one" and (prev in _ORDINALS or prev in _DEMONSTRATIVES):
            continue  # "the second one", "that one": a pronoun, not option 1
        if w == "last":
            found.append(n_options)
        elif w in _STANDALONE:
            found.append(_STANDALONE[w])
        elif w in _NEEDS_NUMBER and ("number" in (prev, nxt) or "nambar" in (prev, nxt)):
            found.append(_NEEDS_NUMBER[w])
        elif w in _NEEDS_NUMBER and len(ws) == 1 and w != "do":
            found.append(_NEEDS_NUMBER[w])  # "ek" or "teen" said alone
    return found


def _card_words(card_text: str) -> set[str]:
    """The fixed words of a card ("Message Mom"), never its quoted free text (a message the agent wrote) and never
    the placeholder for a word we cannot read."""
    fixed = re.sub(r"[“\"«][^”\"»]*[”\"»]?", " ", card_text or "")
    return set(words(fixed)) - {_UNMAPPED}


def decide(transcript: str, n_options: int, *, stage: Stage = "choose", echo: str = "") -> Decision:
    """Decide what *transcript* says about a card set of *n_options*.

    ``choose`` stage: a clear option number picks it; "yes" picks only when there
    is exactly one option. ``final`` stage: only yes or no counts, and every word must be
    known or one of the words of *echo* (the final card's own text).
    """
    ws = words(transcript)
    heard = " ".join(ws)
    unclear = Decision("unclear", None, heard)
    if not ws:
        return unclear

    has_no = any(w in _NO for w in ws) or "mat karo" in heard
    has_yes = any(w in _YES for w in ws) or "theek hai" in heard
    if "kar do" in heard or "kar dijiye" in heard:
        has_yes = True  # "do it" — never a number
    options = _option_mentions(ws, n_options)

    known = _YES | _NO | _FILLER | set(_STANDALONE) | set(_NEEDS_NUMBER) | _ORDINALS | {"last", "nambar"}
    said_back = _card_words(echo) if stage == "final" else set()
    unknown = [w for w in ws if w not in known and w not in said_back]
    if len(unknown) > 2 or (stage == "final" and unknown):
        return unclear  # on the high-risk card even one word we can't read could be a hedge
    # "is it", "can you"... asks a question; "okay, do it" is an instruction. Every match counts, not just the first.
    asks = any(m.group(0) != "do it" for m in re.finditer(
        r"\b(is|are|was|were|do|does|did|can|could|will|should) (it|that|this|he|she|they|you|we)\b", heard))
    time_ask = any(w in ("second", "sec", "seconds", "secs") and i > 0
                   and (ws[i - 1] in _TIME_BEFORE_SECOND or ws[i - 1].isdigit()) for i, w in enumerate(ws))
    if (asks or time_ask or any(w in _UNDECIDED for w in ws)) and not (has_no and not has_yes and not options):
        return unclear  # "ok, hang on", "okay, who is it", "yes but": still deciding (a plain "no, wait" still cancels)

    if has_no:
        if has_yes or options:
            return unclear  # "not two", "no, I said two", "yes no"
        return Decision("no", None, heard)

    distinct = set(options)
    if stage == "final":
        if distinct or not has_yes:
            return unclear
        return Decision("yes", None, heard)

    if len(distinct) > 1:
        return unclear
    if len(distinct) == 1:
        (option,) = distinct
        if not 1 <= option <= n_options:
            return unclear
        return Decision("choose", option, heard)
    if has_yes and n_options == 1:
        return Decision("choose", 1, heard)
    return unclear
