"""The viewer entry point: --host/--port handling and the exposure guard."""

from __future__ import annotations

import pytest

import web_viewer


class FakeApp:
    """Stands in for the Flask app so no socket is ever opened."""

    def __init__(self):
        self.run_kwargs = None

    def run(self, **kwargs):
        self.run_kwargs = kwargs


@pytest.fixture
def fake_app(monkeypatch):
    app = FakeApp()
    monkeypatch.setattr(web_viewer, "create_app", lambda settings: app)
    return app


# ------------------------------------------------------------ argument parsing


def test_host_and_port_default_to_none():
    args = web_viewer.parse_args([])
    assert args.host is None
    assert args.port is None
    assert args.debug is None


def test_explicit_host_and_port():
    args = web_viewer.parse_args(["--host", "0.0.0.0", "--port", "8080"])
    assert (args.host, args.port) == ("0.0.0.0", 8080)


def test_interface_is_an_alias_for_host():
    assert web_viewer.parse_args(["--interface", "192.168.1.5"]).host == "192.168.1.5"


def test_short_port_flag():
    assert web_viewer.parse_args(["-p", "9000"]).port == 9000


def test_non_numeric_port_is_rejected():
    with pytest.raises(SystemExit):
        web_viewer.parse_args(["--port", "http"])


def test_debug_flags_are_mutually_exclusive():
    assert web_viewer.parse_args(["--debug"]).debug is True
    assert web_viewer.parse_args(["--no-debug"]).debug is False
    with pytest.raises(SystemExit):
        web_viewer.parse_args(["--debug", "--no-debug"])


# --------------------------------------------------------------- loopback test


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "127.0.0.5", "localhost", "LOCALHOST", "::1", "0:0:0:0:0:0:0:1"],
)
def test_loopback_hosts(host):
    assert web_viewer.is_loopback(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "10.0.0.1", "::", "", "example.com"])
def test_non_loopback_hosts(host):
    assert web_viewer.is_loopback(host) is False


# ------------------------------------------------------------------ main flow


def test_defaults_come_from_settings(fake_app):
    assert web_viewer.main([]) == 0
    assert fake_app.run_kwargs["host"] == web_viewer.default_settings.host
    assert fake_app.run_kwargs["port"] == web_viewer.default_settings.port


def test_flags_reach_the_server(fake_app):
    assert web_viewer.main(["--host", "127.0.0.1", "--port", "8123"]) == 0
    assert fake_app.run_kwargs["host"] == "127.0.0.1"
    assert fake_app.run_kwargs["port"] == 8123


def test_port_zero_is_allowed(fake_app):
    assert web_viewer.main(["--port", "0"]) == 0
    assert fake_app.run_kwargs["port"] == 0


@pytest.mark.parametrize("port", ["-1", "65536", "99999"])
def test_out_of_range_ports_are_refused(fake_app, port):
    assert web_viewer.main(["--port", port]) == 2
    assert fake_app.run_kwargs is None


def test_binding_publicly_is_allowed_with_a_warning(fake_app, caplog):
    with caplog.at_level("WARNING"):
        assert web_viewer.main(["--host", "0.0.0.0"]) == 0
    assert fake_app.run_kwargs["host"] == "0.0.0.0"
    assert "reachable from outside" in caplog.text


def test_no_warning_for_loopback(fake_app, caplog):
    with caplog.at_level("WARNING"):
        web_viewer.main(["--host", "127.0.0.1"])
    assert "reachable from outside" not in caplog.text


def test_public_debug_is_refused(fake_app):
    """The Werkzeug debugger on a reachable interface is remote code execution."""
    assert web_viewer.main(["--host", "0.0.0.0", "--debug"]) == 2
    assert fake_app.run_kwargs is None


def test_public_debug_can_be_forced(fake_app):
    assert web_viewer.main(
        ["--host", "0.0.0.0", "--debug", "--allow-unsafe-debug"]
    ) == 0
    assert fake_app.run_kwargs["debug"] is True


def test_local_debug_needs_no_override(fake_app):
    assert web_viewer.main(["--host", "127.0.0.1", "--debug"]) == 0
    assert fake_app.run_kwargs["debug"] is True


def test_db_override_is_passed_to_the_app(monkeypatch, tmp_path):
    captured = {}
    app = FakeApp()

    def fake_create_app(settings):
        captured["db_path"] = settings.db_path
        return app

    monkeypatch.setattr(web_viewer, "create_app", fake_create_app)
    target = tmp_path / "other.db"
    assert web_viewer.main(["--db", str(target)]) == 0
    assert captured["db_path"] == target


def test_importing_the_module_builds_no_app(monkeypatch):
    """A bare import must not create a database or an app."""
    calls = []
    monkeypatch.setattr(web_viewer, "create_app", lambda settings: calls.append(1))
    # Accessing an unrelated attribute must not trigger the lazy app.
    assert web_viewer.MAX_PORT == 65535
    assert calls == []


def test_wsgi_app_attribute_is_built_on_access(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(web_viewer, "create_app", lambda settings: sentinel)
    monkeypatch.delitem(web_viewer.__dict__, "app", raising=False)
    assert web_viewer.__getattr__("app") is sentinel


def test_unknown_module_attribute_still_raises():
    with pytest.raises(AttributeError):
        web_viewer.__getattr__("not_a_real_attribute")
