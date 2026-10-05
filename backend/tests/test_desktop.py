import socket

from fastapi.testclient import TestClient

from automated_academics import desktop
from automated_academics.api import create_app


def screen(tmp_path):
    d = tmp_path / "screen"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text('<div id="root"></div>')
    (d / "assets" / "app.js").write_text("console.log(1)")
    return d


def test_screen_is_served_next_to_the_api(tmp_path):
    with TestClient(create_app(str(tmp_path / "t.db"), static_dir=str(screen(tmp_path)))) as c:
        assert 'id="root"' in c.get("/").text
        assert c.get("/assets/app.js").status_code == 200
        assert c.get("/health").json()["status"] == "ok"  # API routes still win over the screen
        assert c.get("/institutions").json() == []
        assert c.get("/no-such-thing").status_code == 404


def test_no_screen_means_api_only(tmp_path):
    with TestClient(create_app(str(tmp_path / "t.db"), static_dir=str(tmp_path / "missing"))) as c:
        assert c.get("/").status_code == 404 and c.get("/health").status_code == 200


def test_data_folder_follows_aa_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AA_HOME", str(tmp_path / "mine"))
    assert desktop.data_dir() == tmp_path / "mine" and (tmp_path / "mine").is_dir()


def test_port_helpers():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        busy = s.getsockname()[1]
        assert not desktop.is_free(busy)
        assert not desktop.is_ours(busy)  # something is listening-ish, but it is not this app
    assert desktop.is_free(desktop.any_free_port())
