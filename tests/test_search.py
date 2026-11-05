"""Search: keyword and meaning merged, re-ranked, duplicates shown once, the question's words highlighted;
and embedding: every passage gets a vector, kept in step as files change."""

import os
import shutil

import pytest
from fakes import FakeEmbedder, FakeReranker

from homedocs.core import indexer, library, store
from homedocs.core.indexer import Stopped, embed_pass, index_pass
from homedocs.core.search import Searcher, fuse


@pytest.fixture
def docs(hd, tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "car.md").write_text("# Car insurance\n\nThe deductible is five hundred dollars per claim.\n")
    (root / "lease.txt").write_text("Rent is due on the first of the month. Renewing the lease needs notice.\n")
    (root / "wifi.md").write_text("# Wifi\n\nThe router password is on the sticker.\n")
    library.add_folder(root)
    index_pass()
    return root


@pytest.fixture
def db(hd):
    d = hd.open_db()
    yield d
    d.close()


def searcher():
    s = Searcher(FakeEmbedder(), FakeReranker())
    s.refresh()
    return s


def test_fusion_favours_what_both_lists_rank_well():
    assert fuse([1, 2, 3], [3, 4, 1])[:2] == [1, 3]
    assert fuse([5], []) == [5]
    assert fuse([], []) == []


def test_every_passage_gets_a_vector(docs, db):
    assert embed_pass(FakeEmbedder()) == 3
    assert db.execute("SELECT COUNT(*) FROM vectors WHERE model = 'fake-embedder'").fetchone()[0] == 3
    assert embed_pass(FakeEmbedder()) == 0  # nothing left to do


def test_vectors_follow_the_files(docs, db):
    embed_pass(FakeEmbedder())
    (docs / "car.md").write_text("# Car insurance\n\nThe deductible went up to six hundred.\n")
    os.utime(docs / "car.md", (1, 2_000_000_000))
    os.remove(docs / "wifi.md")
    shutil.move(docs / "lease.txt", docs / "flat lease.txt")
    index_pass()
    assert db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 1  # the moved file kept its vector
    assert embed_pass(FakeEmbedder()) == 1  # the changed file's new passage
    orphans = "SELECT COUNT(*) FROM vectors WHERE chunk_id NOT IN (SELECT id FROM chunks)"
    assert db.execute(orphans).fetchone()[0] == 0


def test_a_copy_of_a_passage_reuses_its_vector(docs, db):
    class Counting(FakeEmbedder):
        texts = 0

        def embed(self, texts, batch=8):
            Counting.texts += len(texts)
            return super().embed(texts, batch)

    for i in range(3):  # three copies of a file in other folders: the same name and text
        (docs / f"copy{i}").mkdir()
        shutil.copy(docs / "car.md", docs / f"copy{i}" / "car.md")
    index_pass()
    assert embed_pass(Counting()) == 6  # every passage has a vector...
    assert Counting.texts == 3  # ...but the copies' text was embedded once
    vecs = db.execute(
        "SELECT DISTINCT v.vec FROM vectors v JOIN chunks c ON c.id = v.chunk_id JOIN files f ON f.id = c.file_id "
        "WHERE f.name = 'car.md'"
    ).fetchall()
    assert len(vecs) == 1


def test_another_models_vectors_are_made_again(docs, db):
    embed_pass(FakeEmbedder())

    class Other(FakeEmbedder):
        key = "other-model"

    assert embed_pass(Other()) == 3
    assert {r[0] for r in db.execute("SELECT DISTINCT model FROM vectors")} == {"other-model"}


def test_embedding_stops_between_batches(docs, db, monkeypatch):
    monkeypatch.setattr(indexer, "EMBED_BATCH", 1)  # 2 passages a round
    rounds = []
    with pytest.raises(Stopped):
        embed_pass(FakeEmbedder(), should_stop=lambda: len(rounds) >= 1, report=lambda **i: rounds.append(i))
    assert db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 2
    rounds.clear()
    assert embed_pass(FakeEmbedder(), report=lambda **i: rounds.append(i)) == 1  # carries on...
    assert (rounds[0]["done"], rounds[0]["todo"]) == (2, 3)  # ...and so does its progress (not back to 0%)


def test_a_question_finds_the_passage_that_answers_it(docs):
    embed_pass(FakeEmbedder())
    found = searcher().search("what is the deductible for my car?")
    assert found.by_meaning and found.results[0].name == "car.md"
    best = found.results[0]
    assert best.heading == "Car insurance" and best.where() == "Car insurance"
    assert [best.text[s:e] for s, e in best.spans] == ["Car", "deductible"]  # (not "is", "the"...)


def test_words_are_highlighted_in_their_other_forms(docs):
    embed_pass(FakeEmbedder())
    r = next(r for r in searcher().search("renew my lease").results if r.name == "lease.txt")
    assert {r.text[s:e] for s, e in r.spans} == {"Renewing", "lease"}


def test_without_the_models_it_searches_by_keyword(docs):
    found = Searcher().search("router password")
    assert not found.by_meaning and [r.name for r in found.results] == ["wifi.md"]


def test_a_question_with_any_of_its_words_finds_passages(docs, db):
    assert store.keyword_search(db, "when is the rent due for wifi", every=False)
    assert not store.keyword_search(db, "when is the rent due for wifi", every=True)  # (no passage has both)
    assert store.query_words("What is the deductible?") == ["deductible"]
    assert store.query_words("Who is it") == ["who", "is", "it"]  # (nothing but common words: all of them)


def test_identical_files_are_shown_once(docs):
    shutil.copy(docs / "wifi.md", docs / "wifi copy.md")
    index_pass()
    embed_pass(FakeEmbedder())
    names = [r.name for r in searcher().search("router password").results]
    assert len([n for n in names if n.startswith("wifi")]) == 1


def test_pages_are_said_in_words():
    from homedocs.core.search import Result

    r = Result(1, "/a.pdf", "a.pdf", 4, 4, "Intro", "text", False, 0.0)
    assert r.where() == "Page 4"
    r.page_end = 5
    assert r.where() == "Pages 4-5"
