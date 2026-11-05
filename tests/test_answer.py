"""Answering: when the model is asked, what it's given, the answer streaming in, stopping, and saved
questions reopening as they were."""

import threading

from fakes import FakeEmbedder, FakeLLM

from homedocs.core import answer as answers
from homedocs.core.answer import NOT_FOUND, Answerer, pick, prompt, should_ask, tidy
from homedocs.core.config import ANSWER_CHARS, NOT_FOUND_SCORE
from homedocs.core.search import Found, Result


def result(cid, text, score=0.0, name=None):
    name = name or f"file{cid}.md"
    return Result(cid, f"/docs/{name}", name, None, None, "Rent > Late fees", text, False, score)


def found(*results, by_meaning=True):
    return Found(list(results), 0.1, by_meaning)


LATE = "Rent paid after the 5th is late, and a late fee of $75 is charged. Rent is $1,850."


def test_the_model_is_skipped_when_nothing_could_answer():
    assert not should_ask(found())
    assert not should_ask(found(result(1, LATE, NOT_FOUND_SCORE - 0.1)))
    assert should_ask(found(result(1, LATE, NOT_FOUND_SCORE + 0.1)))
    assert should_ask(found(result(1, LATE, -50), by_meaning=False))  # (keyword only: no re-ranker scores)


def test_the_prompt_numbers_the_best_passages_and_keeps_the_instructions_apart():
    rs = [result(i, f"Passage number {i}.") for i in range(1, 6)]
    first, rest = prompt("How late is late?", rs[:3])
    assert first.startswith("<|im_start|>system\n") and "Not in your files." in first
    assert "Passage number" not in first and "How late" not in first  # (the same every time: read once)
    assert "[1] file1.md — Rent > Late fees\nPassage number 1." in rest and "[3] file3.md" in rest
    assert "[4]" not in rest
    assert rest.endswith("Question: How late is late?<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n")


def test_the_model_reads_the_best_passages_that_fit_in_its_budget():
    big, mid, small = "x" * (ANSWER_CHARS - 100), "y" * 300, "z" * 50
    rs = [result(1, big), result(2, mid), result(3, small), result(4, small), result(5, small)]
    assert [r.chunk_id for r in pick(rs)] == [1, 3, 4]  # (2 doesn't fit after 1; the short ones do)
    assert [r.chunk_id for r in pick(rs[1:])] == [2, 3, 4, 5]
    assert pick([]) == []


def test_a_very_long_passage_is_still_read_but_cut():
    rs = [result(1, "word " * 2000), result(2, "short")]
    assert [r.chunk_id for r in pick(rs)] == [1]
    _, rest = prompt("q", pick(rs))
    assert len(rest) < ANSWER_CHARS + 300


def test_tidy():
    assert tidy("  The fee is $75.  ") == "The fee is $75."
    assert tidy("Not in your files. The passages talk about rent.") == NOT_FOUND
    assert tidy("<think>hmm</think>The fee is $75.") == "The fee is $75."
    # the same in the model's own words
    assert tidy("The car's license plate number is not provided in the passages.") == NOT_FOUND
    assert tidy("The passages don't mention a license plate.") == NOT_FOUND
    assert tidy("Repairs are not specified. Not in your files.") == NOT_FOUND
    partial = "Rent is $1,850. The due date is not mentioned in the passages."
    assert tidy(partial) == partial


def test_an_answer_streams_in_and_its_sentence_is_backed():
    llm = FakeLLM()
    a = Answerer(lambda: llm)
    seen = []
    got = a.answer("How much is the late fee?", found(result(1, LATE, 3.0), result(2, "Other.")), None, seen.append)
    assert got.text == "Rent paid after the 5th is late, and a late fee of $75 is charged."
    assert seen[0] == "Rent" and seen[-1] == got.text  # (word by word)
    assert got.sentences[0].backed and got.sentences[0].passage == 0
    assert len(llm.prompts) == 1 and "[2] file2.md" in llm.prompts[0][1]


def test_not_in_your_files_without_loading_the_model():
    loads = []
    a = Answerer(lambda: loads.append(1) or FakeLLM())
    got = a.answer("Passport?", found(result(1, LATE, NOT_FOUND_SCORE - 3)), None)
    assert got.text == NOT_FOUND and not got.asked_model and got.not_found
    assert loads == [] and not a.loaded


def test_stopping_ends_the_answer_with_nothing():
    llm = FakeLLM(delay=0.05)
    a = Answerer(lambda: llm)
    threading.Timer(0.1, a.stop).start()
    assert a.answer("Late fee?", found(result(1, LATE, 3.0)), None) is None


def test_a_saved_answer_reopens_as_it_was(hd):
    db = hd.open_db()
    with db:
        db.execute(
            "INSERT INTO files (path, name, size, mtime, status) VALUES ('/docs/lease.md', 'lease.md', 1, 1, 'x')"
        )
        for cid, text in ((1, LATE), (2, "Pets: one cat."), (3, "Parking: space 14.")):
            db.execute(
                "INSERT INTO chunks (id, file_id, seq, char_start, char_end, text) VALUES (?, 1, ?, 0, 1, ?)",
                (cid, cid, text),
            )
            db.execute("INSERT INTO chunks_fts (rowid, name, heading, text) VALUES (?, 'lease.md', '', ?)", (cid, text))
    shown = [result(1, LATE, 3.0, "lease.md"), result(2, "Pets: one cat.", 1.0, "lease.md")]
    a = Answerer(lambda: FakeLLM()).answer("Late fee?", found(*shown), FakeEmbedder())
    a.text += " Yes."
    a.sentences = answers.find_sources(a.text, a.passages)
    qid = answers.save(db, a, shown)
    assert answers.recent(db) == [(qid, "Late fee?")]

    again, passages = answers.load(db, qid)
    assert again.text == a.text and [p.chunk_id for p in passages] == [1, 2]
    assert again.sentences[0].backed and again.sentences[0].span == a.sentences[0].span
    assert not again.sentences[1].checked
    assert passages[0].spans  # (the question's words are highlighted again)

    with db:  # the backing passage goes: the sentence keeps no link, and isn't called unbacked
        db.execute("DELETE FROM chunks WHERE id = 1")
    again, passages = answers.load(db, qid)
    assert [p.chunk_id for p in passages] == [2]
    assert not again.sentences[0].backed and not again.sentences[0].checked
    db.close()


def test_a_question_asked_twice_is_listed_once(hd):
    db = hd.open_db()
    a = answers.Answer("Late fee?", NOT_FOUND, [], [])
    answers.save(db, a, [])
    answers.save(db, answers.Answer("Pets?", NOT_FOUND, [], []), [])
    newest = answers.save(db, answers.Answer("late fee? ", NOT_FOUND, [], []), [])
    assert [q for _, q in answers.recent(db)][0] == "late fee? "
    assert len(answers.recent(db)) == 2 and answers.recent(db)[0][0] == newest
    db.close()
