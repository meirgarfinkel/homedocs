"""The eval's plumbing, with the fake models and the household documents (nothing downloaded): the real
numbers come from running it with the real models (see "Evals and tuning" in ARCHITECTURE.md)."""

from fakes import FakeEmbedder, FakeLLM, FakeReranker

from homedocs.core import evals

GOVERNMENT = {"p501--2024.pdf", "p523--2024.pdf", "p530--2024.pdf", "p936--2024.pdf", "10050-medicare-and-you.pdf"}


def household(questions):
    return [q for q in questions if not any(n["file"] in GOVERNMENT for n in q.needs)]


def test_the_questions_are_well_formed():
    qs = evals.load_questions() + evals.load_questions(held_back=True)
    assert len({q.id for q in qs}) == len(qs) > 100
    kinds = {"fact", "table", "two-passages", "two-docs", "page-break", "reworded", "calc", "not-found", "general"}
    assert {q.type for q in qs} == kinds
    for q in qs:
        assert bool(q.needs) == bool(q.facts) == q.answerable, q.id
    held = sum(q.held_back for q in qs)
    assert 0.25 < held / len(qs) < 0.35


def test_every_quote_is_in_its_household_document(tmp_path):
    docs = evals.make_documents(tmp_path, government=False)
    qs = household(evals.load_questions() + evals.load_questions(held_back=True))
    assert evals.check_quotes(qs, docs) == []


def test_a_run_scores_each_step_and_blames_the_first_one_lost(hd, tmp_path, monkeypatch):
    monkeypatch.setenv("HOMEDOCS_HOME", str(tmp_path / "data"))  # (restored after: the run points it elsewhere)
    docs = evals.make_documents(tmp_path / "docs", government=False)
    qs = household(evals.load_questions())[:12]
    outcomes = evals.run(qs, docs, tmp_path / "data", FakeEmbedder(), FakeReranker(), FakeLLM)
    assert len(outcomes) == len(qs)
    for o in outcomes:
        assert o.steps["split"] or not o.q.answerable  # (every quote is inside a passage)
        assert set(o.steps) == {"split", "search", "pick", "answer"}
        if o.lost_at:
            assert all(o.steps[s] for s in evals.STEPS[: evals.STEPS.index(o.lost_at)])
    text = evals.report(outcomes, answered=True)
    assert "Right:" in text and "First word:" in text and "Lost at:" in text
    # each answer keeps what a trust rating could go by, and the report says how well each tells right from wrong
    assert all(set(o.signals) == set(evals.SIGNALS) for o in outcomes if o.answer)
    assert evals.trust_report(outcomes).startswith("Trust signals")
    # the index is kept for the next run
    assert (tmp_path / "data" / "indexed").exists()


def test_search_only_runs_without_a_chat_model(hd, tmp_path, monkeypatch):
    monkeypatch.setenv("HOMEDOCS_HOME", str(tmp_path / "data"))
    docs = evals.make_documents(tmp_path / "docs", government=False)
    qs = household(evals.load_questions())[:5]
    outcomes = evals.run(qs, docs, tmp_path / "data", FakeEmbedder(), FakeReranker())
    assert all("answer" not in o.steps and not o.answer for o in outcomes)
    assert "Needed passages read by the model" in evals.report(outcomes, answered=False)


def test_an_answer_is_right_when_it_states_every_fact():
    q = evals.Question(1, "How much is rent?", "fact", [{"file": "x", "quote": "y"}], ["1850", "first"], False)
    assert evals.right_answer(q, "Rent is $1,850, due on the first of the month.")
    assert not evals.right_answer(q, "Rent is $1,850.")
    assert not evals.right_answer(q, "Not in your files.")
    none = evals.Question(2, "When does my passport expire?", "not-found", [], [], False)
    assert evals.right_answer(none, "Not in your files.")
    assert not evals.right_answer(none, "It expires in 2030.")


def test_a_setting_changes_everywhere_it_was_imported():
    from homedocs.core import answer, config

    old = config.ANSWER_CHARS
    try:
        assert evals.set_setting("ANSWER_CHARS", "900") == 900
        assert answer.ANSWER_CHARS == config.ANSWER_CHARS == 900
    finally:
        evals.set_setting("ANSWER_CHARS", old)
    assert old == answer.ANSWER_CHARS


def test_a_number_the_passages_dont_have_is_a_stray():
    from homedocs.core.answer import Answer, Sentence
    from homedocs.core.search import Found, Result

    passage = Result(
        1, "/d/lease.md", "lease.md", None, None, None, "Pet rent is $35 a month; the fee is $300.", 0, 2.0
    )

    def signals(text):
        a = Answer("Dog cost for 12 months?", text, [passage], [Sentence(0, len(text), 0, (0, 5), True, 1.2)])
        return evals.trust_signals(a.question, a, Found([passage], 0.1, True))

    assert signals("$300 + 12 × $35 = $720.")["no_stray_numbers"] == 0  # (a sum's result, the question's 12)
    assert signals("It costs $460 a year.")["no_stray_numbers"] == -1
    assert signals("It's $1,850.00 or 3.50.")["no_stray_numbers"] == -2
