"""Command dispatch, argument handling and the augment loop."""

from __future__ import annotations

import pytest

from ctrlfvuln import cli
from ctrlfvuln.db import STARS_UNAVAILABLE, connect


@pytest.fixture
def run(monkeypatch, settings):
    """Invoke the CLI with the test settings in place."""

    def invoke(argv: list[str]) -> int:
        monkeypatch.setattr(cli, "default_settings", settings)
        return cli.main(argv)

    return invoke


# ----------------------------------------------------------- argument parsing


def test_projectless_commands_need_no_project():
    args = cli.parse_args(["config"])
    assert args.command == "config"


def test_project_and_command_order_is_unchanged():
    args = cli.parse_args(["php-search", "search", "--query", "x"])
    assert (args.project, args.command, args.query) == ("php-search", "search", "x")


def test_unknown_command_exits():
    with pytest.raises(SystemExit):
        cli.parse_args(["proj", "frobnicate"])


# ------------------------------------------------------------------ guardrails


def test_search_without_a_query_is_rejected(run):
    assert run(["php-search", "search", "--token", "t"]) == 2


def test_token_requirement_is_reported_not_crashed(run, monkeypatch, settings):
    """Without a token this used to raise TypeError inside python-decouple."""
    monkeypatch.setattr(cli, "default_settings", settings.with_overrides(github_token=None))
    assert cli.main(["php-search", "search", "--query", "x"]) == 2
    assert cli.main(["php-search", "augment"]) == 2


# ----------------------------------------------------------------- read paths


def test_stats_reports_counts(run, capsys):
    assert run(["php-search", "stats"]) == 0
    output = capsys.readouterr().out
    assert "Files:" in output
    assert "php-search" in output
    assert "augment" in output  # Nudge about the repo with no star count.


def test_stats_for_an_unknown_project(run, capsys):
    assert run(["nope", "stats"]) == 1
    assert "Known projects" in capsys.readouterr().out


def test_list_prints_repositories(run, capsys):
    assert run(["php-search", "list"]) == 0
    output = capsys.readouterr().out
    assert "alice/shop" in output
    assert "carol/legacy" in output


def test_list_marks_unknown_star_counts(run, capsys):
    run(["php-search", "list"])
    assert "?" in capsys.readouterr().out


def test_list_for_an_unknown_project(run):
    assert run(["nope", "list"]) == 1


def test_migrate_is_reported(run, capsys):
    assert run(["-", "migrate"]) == 0
    assert "Journal mode" in capsys.readouterr().out


def test_config_writes_a_template(run, settings, capsys):
    assert run(["-", "config"]) == 0
    assert settings.env_path.exists()
    assert "GITHUB_TOKEN" in settings.env_path.read_text()

    # A second run leaves the existing file alone.
    settings.env_path.write_text("GITHUB_TOKEN=keep-me\n")
    assert run(["-", "config"]) == 0
    assert settings.env_path.read_text() == "GITHUB_TOKEN=keep-me\n"


# --------------------------------------------------------------------- resume


def test_resume_without_an_unfinished_search(run):
    """Regression: main() called run_resume(args, token) against a 1-arg function."""
    assert run(["other", "resume", "--token", "fake"]) == 0


def test_resume_for_an_unknown_project(run):
    assert run(["nope", "resume", "--token", "fake"]) == 1


def test_resume_picks_up_the_stored_cursor(run, monkeypatch, settings):
    captured = {}

    def fake_run_search(conn, client, **kwargs):
        captured.update(kwargs)
        raise KeyboardInterrupt  # Stop before any network use.

    monkeypatch.setattr(cli, "run_search", fake_run_search)
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE Searches SET size_cursor = 512, size_step = 64 WHERE id = 2")

    assert run(["php-search", "resume", "--token", "fake"]) == 130
    assert captured["size_cursor"] == 512
    assert captured["size_step"] == 64
    assert captured["query"]


def test_an_interrupted_search_stays_resumable(run, monkeypatch, settings):
    monkeypatch.setattr(
        cli, "run_search", lambda conn, client, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt)
    )
    assert run(["php-search", "search", "--query", "needle", "--token", "fake"]) == 130

    with connect(settings.db_path) as conn:
        unfinished = conn.execute(
            "SELECT COUNT(*) FROM Searches WHERE finished = 0"
        ).fetchone()[0]
    assert unfinished >= 1


# -------------------------------------------------------------------- augment


class BoundedClient:
    """A GitHub stand-in that fails the test rather than looping forever."""

    def __init__(self, stars: dict[str, int | None], *, max_calls: int = 25):
        self.stars = stars
        self.max_calls = max_calls
        self.seen: list[str] = []

    def get_repo(self, full_name: str):
        self.seen.append(full_name)
        if len(self.seen) > self.max_calls:
            raise AssertionError(f"augment did not terminate: {self.seen}")
        value = self.stars.get(full_name, "missing")
        if value == "missing":
            return None
        return {"stargazers_count": value}


def test_augment_fills_in_star_counts(run, monkeypatch, settings):
    client = BoundedClient({"carol/legacy": 77})
    monkeypatch.setattr(cli, "_client", lambda _settings: client)

    assert run(["php-search", "augment", "--token", "fake"]) == 0

    with connect(settings.db_path) as conn:
        stars = conn.execute(
            "SELECT stargazers_count FROM Repositories WHERE id = 12"
        ).fetchone()[0]
    assert stars == 77
    assert client.seen == ["carol/legacy"]


def test_a_deleted_repository_does_not_loop_forever(run, monkeypatch, settings):
    """Regression: a 404 left stargazers_count at -1, so the batch reselected it."""
    client = BoundedClient({})  # Every lookup 404s.
    monkeypatch.setattr(cli, "_client", lambda _settings: client)

    assert run(["php-search", "augment", "--token", "fake"]) == 0

    with connect(settings.db_path) as conn:
        stars = conn.execute(
            "SELECT stargazers_count FROM Repositories WHERE id = 12"
        ).fetchone()[0]
    assert stars == STARS_UNAVAILABLE
    assert len(client.seen) == 1


def test_augment_is_scoped_to_the_project(run, monkeypatch, settings):
    """Repo 12 belongs to php-search only, so the 'other' project has no work."""
    client = BoundedClient({"carol/legacy": 5})
    monkeypatch.setattr(cli, "_client", lambda _settings: client)

    assert run(["other", "augment", "--token", "fake"]) == 0
    assert client.seen == []


def test_augment_all_projects(run, monkeypatch):
    client = BoundedClient({"carol/legacy": 5})
    monkeypatch.setattr(cli, "_client", lambda _settings: client)

    assert run(["other", "augment", "--token", "fake", "--all-projects"]) == 0
    assert client.seen == ["carol/legacy"]


def test_augment_for_an_unknown_project(run):
    assert run(["nope", "augment", "--token", "fake"]) == 1
