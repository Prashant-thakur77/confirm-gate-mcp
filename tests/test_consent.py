"""What the user's confirmation words decide. False accepts are the failure that matters."""

from __future__ import annotations

import pytest
from confirm_gate.consent import decide, normalise


@pytest.mark.parametrize(
    "said, option",
    [
        ("two", 2),
        ("Two.", 2),
        ("2", 2),
        ("number two", 2),
        ("option 3", 3),
        ("the second one", 2),
        ("the first one", 1),
        ("one", 1),
        ("number one", 1),
        ("1st", 1),
        ("the third", 3),
        ("the last one", 3),
        ("two please", 2),
        ("yes, two", 2),
        ("two, yes", 2),
        ("I want number three", 3),
        # Hinglish and Devanagari
        ("pehla", 1),
        ("doosra", 2),
        ("dusra wala", 2),
        ("teesra", 3),
        ("do number", 2),
        ("number do", 2),
        ("teen number", 3),
        ("ek number", 1),
        ("दो नंबर", 2),
        ("दूसरा", 2),
        ("टू", 2),
        ("नंबर थ्री", 3),
        ("२", 2),
        ("नंबर त्वो", 2),
    ],
)
def test_a_clear_choice_picks_that_option(said: str, option: int) -> None:
    d = decide(said, 3)
    assert d.kind == "choose", (said, d)
    assert d.option == option


@pytest.mark.parametrize(
    "said",
    [
        "",
        "um",
        "do",  # English "do" and Hindi "do" (give / two) — never a number on its own
        "isko kar do",  # "please do this", not a choice of option 2
        "four",  # out of range for three options
        "zero",
        "yes",  # which one? yes alone cannot pick among three
        "that one",
        "this one",
        "one or two",
        "two three",
        "not two",
        "no two",
        "no, I said two",
        "yes no",
        "turn on the fan",
        "to",  # a likely mishearing of "two" is still not "two"
        "maybe",
    ],
)
def test_anything_unclear_is_refused_not_guessed(said: str) -> None:
    d = decide(said, 3)
    assert d.kind == "unclear", (said, d)
    assert d.option is None


@pytest.mark.parametrize(
    "said", ["no", "No.", "nope", "cancel", "none of these", "neither", "stop", "nahin", "nahi", "mat karo", "ruko", "नहीं"]
)
def test_no_cancels(said: str) -> None:
    d = decide(said, 3)
    assert d.kind == "no", (said, d)


@pytest.mark.parametrize("said", ["yes", "yeah", "yep", "yes please", "confirm", "okay", "haan", "haan ji", "theek hai", "हाँ", "हां", "येस।"])
def test_yes_confirms_the_final_card(said: str) -> None:
    assert decide(said, 1, stage="final").kind == "yes", said


@pytest.mark.parametrize("said", ["two", "one", "number one"])
def test_a_number_is_not_a_yes_on_the_final_card(said: str) -> None:
    assert decide(said, 1, stage="final").kind == "unclear"


def test_yes_picks_the_only_option_when_there_is_one() -> None:
    d = decide("yes", 1)
    assert d.kind == "choose" and d.option == 1


def test_no_on_the_final_card_cancels() -> None:
    assert decide("no", 1, stage="final").kind == "no"


def test_too_many_other_words_means_it_was_not_a_confirmation() -> None:
    assert decide("order two of the blue cables now", 3).kind == "unclear"


def test_normalise_maps_devanagari_and_digits() -> None:
    assert normalise("नंबर टू।") == "number 2"
    assert normalise("Option Three!") == "option 3"


def test_hindi_you_is_never_two() -> None:
    assert decide("तू", 3).kind == "unclear"


@pytest.mark.parametrize("said", [
    "ok hang on", "hold on okay", "sure, in a minute", "okay, later", "ok baad mein", "okay who is it",
    "yes is it Ravi", "yes wait", "haan ruko zara", "हाँ लेकिन अभी दरवाज़ा खोलने से पहले पूछो कि कौन है",
    "yes but", "okay what", "haan kaun hai",
])
def test_hesitations_and_questions_are_never_a_yes(said: str) -> None:
    """Security review finding: deferrals and questions were accepted on the final (high-risk) card."""
    assert decide(said, 1, stage="final").kind != "yes", said
    assert decide(said, 1).kind != "choose", said


@pytest.mark.parametrize("said", ["wait a second", "hold on, number two", "two? wait", "later, the first one"])
def test_hesitation_blocks_a_choice_too(said: str) -> None:
    assert decide(said, 3).kind == "unclear", said


def test_unmapped_devanagari_counts_as_unknown_words() -> None:
    assert decide("हाँ लेकिन पहले पूछो", 1, stage="final").kind == "unclear"


@pytest.mark.parametrize("said", [
    "just a second", "a second please", "in a second", "ek second", "एक सेकंड", "haan ek second",
    "one second", "half a second", "give me a sec", "a few seconds",
])
def test_a_second_of_time_is_not_option_two(said: str) -> None:
    """Second review, finding 3: "ek second" (wait a moment) was read as "option 2" and turned on the TV."""
    assert decide(said, 3).kind == "unclear", said
    assert decide(said, 1, stage="final").kind != "yes", said


@pytest.mark.parametrize("said", [
    "yeah, unsure", "maybe yes", "probably yes", "perhaps", "yes I think so", "yes, actually", "okay, tomorrow",
    "yes, in a bit", "haan shayad", "हाँ शायद", "haan, sochne do", "हाँ सोचने दो", "haan ruk jao", "हाँ रुकिए",
    "haan thehro", "yes I guess", "okay soon", "haan kal", "yes, might",
])
def test_hedges_are_never_a_yes_on_the_final_card(said: str) -> None:
    assert decide(said, 1, stage="final").kind != "yes", said
    assert decide(said, 1).kind != "choose", said


@pytest.mark.parametrize("said", ["yes, frobnicate", "haan ज़बरदस्त", "okay qwerty"])
def test_the_final_card_allows_no_unknown_words(said: str) -> None:
    """One unknown word could be a hedge in a language we don't map; on the high-risk card that is unclear."""
    assert decide(said, 1, stage="final").kind == "unclear", said


@pytest.mark.parametrize("said", [
    "yes", "yes please", "yes go ahead", "yes, that's correct", "haan bilkul", "yes definitely", "haan ji theek hai",
    "okay do it", "haan kar do", "yes absolutely",
])
def test_plain_and_emphatic_yeses_still_count_on_the_final_card(said: str) -> None:
    assert decide(said, 1, stage="final").kind == "yes", said


@pytest.mark.parametrize("said, option", [("the second one", 2), ("second", 2), ("number two please", 2),
                                          ("doosra wala", 2), ("सेकंड", 2), ("second option", 2)])
def test_the_ordinal_second_still_picks_two(said: str, option: int) -> None:
    assert decide(said, 3) == decide(said, 3).__class__("choose", option, decide(said, 3).heard), said


def test_on_the_final_card_the_cards_own_words_are_not_unknown() -> None:
    card = "Unlock the front door"
    assert decide("yes, unlock the front door", 1, stage="final", echo=card).kind == "yes"
    assert decide("yes, unlock it", 1, stage="final", echo=card).kind == "yes"
    assert decide("yes, unlock it", 1, stage="final").kind == "unclear"
    assert decide("yes, unlock it later", 1, stage="final", echo=card).kind == "unclear"
    assert decide("yes, unlock the back door", 1, stage="final", echo=card).kind == "unclear"


@pytest.mark.parametrize("said, card", [
    ("हाँ रहने दो", "Message Mom: “मैं घर आ रहा हूँ”"),
    ("yes, nevermind", "Message Mom: “scratch that, kidding, nevermind”"),
    ("yes scratch that", "Message Mom: “scratch that, kidding, nevermind”"),
])
def test_quoted_text_and_unreadable_words_on_a_card_are_never_allowed_back(said: str, card: str) -> None:
    """Third review, finding 1: the echo let a message's own words (or any unreadable Hindi word) pass a final yes."""
    assert decide(said, 1, stage="final", echo=card).kind == "unclear", said


def test_the_cards_fixed_words_are_still_allowed_back() -> None:
    assert decide("yes, message mom", 1, stage="final", echo="Message Mom: “I'll be late”").kind == "yes"


@pytest.mark.parametrize("said, stage", [
    ("okay, do it? is it the front door?", "final"), ("yes do it, is it the front door", "final"),
    ("two, do it, is it safe", "choose"),
])
def test_do_it_does_not_hide_a_later_question(said: str, stage: str) -> None:
    """Third review, finding 2: only the first question-shaped match was checked."""
    n = 1 if stage == "final" else 3
    assert decide(said, n, stage=stage).kind == "unclear", said  # type: ignore[arg-type]


@pytest.mark.parametrize("said", [
    "two, nevermind", "number two, rehne do", "नंबर दो, रहने दो", "two, chhodo", "two, scratch that", "two I suppose",
    "two, dunno", "two, sorry", "two second", "bas do second", "10 second", "another second", "two more seconds",
])
def test_hedges_and_time_phrases_never_pick_a_card(said: str) -> None:
    """Third review, finding 6."""
    assert decide(said, 3).kind == "unclear", said


# Found by eval/run_consent_stress.py (7,045 generated non-answers): questions and a misheard "the two one".
@pytest.mark.parametrize("said", [
    "is two the fan", "Is two the fan.", "which one is two", "what is option two", "is it the first one",
    "can you do number two", "does one turn on the fan", "kya do number fan hai", "kaun sa doosra hai",
])
def test_a_question_without_a_question_mark_picks_nothing(said: str) -> None:
    # recognisers often drop the "?"; a question about a card is not a choice of it
    assert decide(said, 3).kind == "unclear"


@pytest.mark.parametrize("said", ["the to one", "the tu one", "the free one", "the won one", "that tree one"])
def test_one_after_the_and_an_unknown_word_is_a_pronoun_not_option_one(said: str) -> None:
    # "the to one" is a misheard "the two one": refuse it, never read it as option one
    assert decide(said, 3).kind == "unclear"


@pytest.mark.parametrize("said, option", [("doosra one", 2), ("teesra one", 3), ("pehla one", 1), ("the second one", 2)])
def test_an_ordinal_then_one_still_picks_the_ordinal(said: str, option: int) -> None:
    d = decide(said, 3)
    assert d.kind == "choose" and d.option == option


@pytest.mark.parametrize("said", ["go ahead", "Go ahead.", "yes, go ahead", "haan, go ahead"])
def test_go_ahead_is_a_yes_on_the_final_card(said: str) -> None:
    assert decide(said, 1, stage="final").kind == "yes"


@pytest.mark.parametrize("said", ["go ahead, wait", "go ahead later", "maybe go ahead", "don't go ahead", "go ahead? which one"])
def test_go_ahead_with_a_hedge_or_a_no_is_not_a_yes(said: str) -> None:
    assert decide(said, 1, stage="final").kind in ("unclear", "no")


# Fourth review, findings 1–3.
@pytest.mark.parametrize("said", ["I wouldn't go ahead", "we can't go ahead", "i cannot go ahead", "I wouldn't say yes",
                                  "shouldn't do number two", "I won't pick two", "didnt mean two"])
def test_a_negated_answer_is_never_consent(said: str) -> None:
    assert decide(said, 1).kind in ("unclear", "no")
    assert decide(said, 3).kind in ("unclear", "no")


@pytest.mark.parametrize("said", ["um, is two the fan", "okay is two the fan", "so is two the fan", "well is two the fan",
                                  "हाँ is two the fan", "accha is two the fan", "uh which one is the fan"])
def test_a_question_after_a_filler_is_still_a_question(said: str) -> None:
    assert decide(said, 3).kind == "unclear"


@pytest.mark.parametrize("said, option", [("the number one", 1), ("that number one please", 1), ("i want the number one", 1),
                                          ("the option one", 1), ("can I have two", 2), ("could I get number two please", 2),
                                          ("can i have the second one", 2)])
def test_polite_and_numbered_answers_still_choose(said: str, option: int) -> None:
    d = decide(said, 3)
    assert d.kind == "choose" and d.option == option
