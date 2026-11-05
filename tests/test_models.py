"""Downloading the models: each file checked against its sha256, a stopped download carried on, a damaged
one thrown away. (The models themselves are tried by hand: tests use fakes.)"""

import hashlib
import http.server
import threading

import pytest

from homedocs.core import models


class _Ranges(http.server.SimpleHTTPRequestHandler):
    """Serves files, honouring Range: bytes=N- like a real download server."""

    def do_GET(self):  # noqa: N802 (the library's name)
        path = self.translate_path(self.path)
        with open(path, "rb") as f:
            data = f.read()
        start = int(self.headers["Range"].split("=")[1].rstrip("-")) if self.headers.get("Range") else 0
        self.send_response(206 if start else 200)
        self.send_header("Content-Length", str(len(data) - start))
        self.end_headers()
        self.wfile.write(data[start:])
        self.server.served.append(start)

    def log_message(self, *a):
        pass


@pytest.fixture
def server(tmp_path):
    served = tmp_path / "served"
    served.mkdir()
    handler = lambda *a, **k: _Ranges(*a, directory=str(served), **k)  # noqa: E731
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.served = []
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield served, f"http://127.0.0.1:{httpd.server_port}", httpd
    httpd.shutdown()


def model(served, url, data=b"model weights " * 250000, sha=None):  # (3.5 MB: a few chunks)
    (served / "model.onnx").write_bytes(data)
    f = models.ModelFile("model.onnx", f"{url}/model.onnx", sha or hashlib.sha256(data).hexdigest(), len(data))
    return models.Model("test-model", "Test model", "MIT", (f,))


@pytest.fixture(autouse=True)
def real_ready(monkeypatch):
    monkeypatch.undo()  # (these tests check the real thing, not the fake models' "always ready")


def test_a_model_is_downloaded_and_checked(hd, server):
    served, url, _ = server
    m = model(served, url)
    seen = []
    assert models.download([m], lambda d, t: seen.append((d, t)))
    assert m.ready() and (m.folder / "model.onnx").read_bytes() == (served / "model.onnx").read_bytes()
    assert seen[-1] == (m.size, m.size)
    assert models.download([m])  # already there: nothing to do


def test_a_stopped_download_carries_on(hd, server):
    served, url, httpd = server
    m = model(served, url)
    calls = []
    assert not models.download([m], should_stop=lambda: calls.append(1) or len(calls) > 1)
    part = m.folder / "model.onnx.part"
    assert part.exists() and not m.ready()
    have = part.stat().st_size
    assert models.download([m])
    assert m.ready() and httpd.served[-1] == have  # asked only for the rest


def test_a_damaged_download_is_thrown_away(hd, server):
    served, url, _ = server
    m = model(served, url, sha="0" * 64)
    with pytest.raises(models.DownloadError, match="damaged"):
        models.download([m])
    assert not m.ready() and not (m.folder / "model.onnx.part").exists()


def test_no_connection_says_so(hd):
    f = models.ModelFile("model.onnx", "http://127.0.0.1:9/nothing", "0" * 64, 10)
    with pytest.raises(models.DownloadError, match="Couldn't download"):
        models.download([models.Model("x", "Test model", "MIT", (f,))])


def test_the_pinned_models():
    for m in models.MODELS:
        assert m.licence in ("MIT", "Apache 2.0")
        for f in m.files:
            assert f.url.startswith("https://huggingface.co/") and "/resolve/" in f.url and len(f.sha256) == 64


def test_a_retired_model_is_deleted(hd):
    old = hd.data_dir() / "models" / models.RETIRED[0]
    old.mkdir(parents=True)
    (old / "model.onnx").write_bytes(b"old")
    keep = hd.data_dir() / "models" / models.EMBEDDER.key
    keep.mkdir(parents=True)
    models.remove_retired()
    assert not old.exists() and keep.exists()
