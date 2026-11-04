"""Finding what backs each sentence of an answer: the same words, reworded, made up, from the wrong passage."""

from fakes import FakeEmbedder

from homedocs.core.answer import NOT_FOUND, find_sources, sentences, words
from homedocs.core.search import Result

LEASE = (
    "Monthly rent is $1,850, due on the first day of each month. Rent paid after the 5th is late, and a late "
    "fee of $75 is charged, plus $10 for each additional day."
)
DISHWASHER = "- **Normal**: everyday loads, about 2 hours 10 minutes.\n- **Quick**: lightly soiled dishes, 45 minutes."
CAR = "The collision deductible is $500 per claim. The comprehensive deductible is $250 per claim."


def passage(text, name="file.md", cid=1):
    return Result(cid, f"/docs/{name}", name, None, None, None, text, False, 0.0)


def test_sentences_are_found_with_their_places():
    text = "Rent is $1,850. It's due on the 1st!\nPay online."
    assert [text[s:e] for s, e in sentences(text)] == ["Rent is $1,850.", "It's due on the 1st!", "Pay online."]
    assert [DISHWASHER[s:e] for s, e in sentences(DISHWASHER)][1] == "- **Quick**: lightly soiled dishes, 45 minutes."


def test_a_line_wrapped_by_hand_is_still_one_sentence():
    text = "Rent paid after the 5th is late, and a late fee of\n$75 is charged.\n\nPets:\n- One cat."
    assert [text[s:e] for s, e in sentences(text)] == [
        "Rent paid after the 5th is late, and a late fee of\n$75 is charged.",
        "Pets:",
        "- One cat.",
    ]


def test_words_keep_amounts_whole_and_drop_common_words():
    assert words("The fee is $1,850 for 16x25x1 filters") == {"fee", "$1850", "16x25x1", "filter"}
    assert words("the dishwasher's cycle") == {"dishwasher", "cycle"}


def test_a_sentence_with_the_passage_words_is_backed_by_that_sentence():
    p = passage(LEASE)
    got = find_sources("A late fee of $75 is charged, plus $10 for each additional day.", [p])
    assert len(got) == 1 and got[0].backed and got[0].passage == 0
    s, e = got[0].span
    assert LEASE[s:e].startswith("Rent paid after the 5th")


def test_the_right_passage_backs_it_when_there_are_several():
    lease, car = passage(LEASE, "lease.md", 1), passage(CAR, "car.md", 2)
    got = find_sources("The collision deductible is $500 per claim.", [lease, car])
    assert got[0].passage == 1 and CAR[got[0].span[0] : got[0].span[1]].startswith("The collision deductible")


def test_a_made_up_sentence_is_not_backed():
    got = find_sources("The landlord repaints the apartment every two years.", [passage(LEASE)], FakeEmbedder())
    assert got[0].checked and not got[0].backed


def test_a_wrong_amount_is_not_backed():
    got = find_sources("The late fee is $95.", [passage(LEASE)], question="How much is the late fee?")
    assert not got[0].backed


def test_words_repeated_from_the_question_need_no_source():
    """Half its words repeat the question; what it adds ("45 minutes") is in the manual."""
    got = find_sources(
        "The dishwasher's quick cycle takes 45 minutes.",
        [passage(DISHWASHER)],
        question="How long does the dishwasher's quick cycle take?",
    )
    assert got[0].backed


def test_meaning_alone_can_back_a_reworded_sentence():
    class Same(FakeEmbedder):
        def embed(self, texts, batch=8):  # every text means the same
            import numpy as np

            return np.ones((len(texts), 4), np.float32) / 2

    reworded = "Payment for housing must reach the owner at the start of every month."
    assert not find_sources(reworded, [passage(LEASE)])[0].backed
    assert find_sources(reworded, [passage(LEASE)], Same())[0].backed


def test_a_sentence_with_nothing_to_check_is_left_alone():
    got = find_sources("Yes. The late fee is $75.", [passage(LEASE)])
    assert not got[0].checked and not got[0].backed
    assert got[1].backed


def test_not_in_your_files_has_no_sentences():
    assert find_sources(NOT_FOUND, [passage(LEASE)]) == []
