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
