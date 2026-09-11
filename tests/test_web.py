"""Page rendering, the JSON API and request-parameter handling."""

from __future__ import annotations

from ctrlfvuln.db import connect
from tests.conftest import COMMA_QUERY


def _checked(db_path, file_id: int) -> int:
    with connect(db_path) as conn:
        return conn.execute(
            "SELECT checked FROM Files WHERE id = ?", (file_id,)
        ).fetchone()["checked"]


# ---------------------------------------------------------------- index page


def test_index_renders(client):
    """With no project given, the first project alphabetically is shown."""
    response = client.get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "php-search" in body  # Present in the project picker.
    assert "eval.php" in body  # Belongs to the default project, "other".


def test_index_renders_the_requested_project(client):
    body = client.get("/?project=php-search").get_data(as_text=True)
    assert "db.php" in body
    assert "eval.php" not in body


def test_index_hides_checked_files_by_default(client):
    body = client.get("/?project=php-search").get_data(as_text=True)
    assert "index.php" not in body
    assert "db.php" in body


def test_index_shows_checked_when_requested(client):
    body = client.get("/?project=php-search&show_checked=on").get_data(as_text=True)
    assert "index.php" in body


def test_pagination_preserves_show_checked(app, settings):
    """Regression: the old pager rebuilt the query string and dropped this flag."""
    paged = app.test_client()
    app.config["SETTINGS"] = settings.with_overrides(page_size=2)
    response = paged.get("/?project=php-search&show_checked=on&min_stars=1&q=php")
    body = response.get_data(as_text=True)
    assert "show_checked=on" in body
    assert "min_stars=1" in body
    assert "q=php" in body


def test_pagination_links_appear_when_paging(app, settings):
    app.config["SETTINGS"] = settings.with_overrides(page_size=2)
    body = app.test_client().get("/?project=php-search&show_checked=on").get_data(as_text=True)
    assert "page=2" in body
    assert "Page 1 of 3" in body


def test_page_beyond_the_end_is_clamped(app, settings):
    app.config["SETTINGS"] = settings.with_overrides(page_size=2)
    response = app.test_client().get("/?project=php-search&show_checked=on&page=99")
    assert response.status_code == 200
    assert "Page 3 of 3" in response.get_data(as_text=True)


def test_sort_links_carry_the_active_filters(client):
    body = client.get("/?project=php-search&q=php&sort=name&direction=asc").get_data(as_text=True)
    assert "sort=path" in body
    assert "q=php" in body


def test_junk_query_parameters_do_not_error(client):
    for query in (
        "?project=php-search&min_stars=abc",
        "?project=php-search&page=notanumber",
        "?project=php-search&page=-5",
        "?project=php-search&sort=;DROP&direction=sideways",
        "?project=php-search&min_stars=99999999999999999999",
    ):
        assert client.get("/" + query).status_code == 200


def test_unknown_project_falls_back_with_a_warning(client):
    response = client.get("/?project=does-not-exist", follow_redirects=True)
    assert response.status_code == 200
    assert "does not exist" in response.get_data(as_text=True)


def test_empty_database_shows_onboarding(empty_settings):
    from ctrlfvuln.web import create_app

    body = create_app(empty_settings).test_client().get("/").get_data(as_text=True)
    assert "No projects yet" in body
    assert "search_github.py" in body


def test_unknown_star_counts_render_as_a_dash(client):
    body = client.get("/?project=php-search").get_data(as_text=True)
    assert "old.php" in body  # From the repo with stargazers_count = -1.
    assert "—" in body


# ------------------------------------------------------------- repo details


def test_repo_details_renders(client):
    response = client.get("/repo/10")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "alice/shop" in body
    assert "Not cloned" in body


def test_repo_details_shows_whole_queries(client):
    """Regression: a query containing a comma used to render as two chips."""
    body = client.get("/repo/10").get_data(as_text=True)
    assert COMMA_QUERY in body


def test_missing_repo_returns_a_styled_404(client):
    response = client.get("/repo/99999")
    assert response.status_code == 404
    assert "Not found" in response.get_data(as_text=True)


def test_editor_buttons_are_disabled_without_an_editor(client):
    body = client.get("/repo/10").get_data(as_text=True)
    assert "No editor found on PATH" in body


# ------------------------------------------------------- no-JavaScript forms


def test_form_post_marks_a_file_checked(client, settings):
    response = client.post("/check/100", data={"checked": "1"})
    assert response.status_code == 302
    assert _checked(settings.db_path, 100) == 1


def test_form_post_can_uncheck(client, settings):
    client.post("/check/101", data={"checked": "0"})
    assert _checked(settings.db_path, 101) == 0


def test_form_post_for_a_missing_file_is_404(client):
    assert client.post("/check/123456", data={"checked": "1"}).status_code == 404


def test_offsite_referer_is_refused_outright(client):
    """The cross-origin guard fires before any redirect is built."""
    response = client.post(
        "/check/100",
        data={"checked": "1"},
        headers={"Referer": "https://evil.example/attack"},
    )
    assert response.status_code == 403


def test_safe_redirect_rejects_offsite_targets(app):
    """Defence in depth behind the cross-origin guard."""
    from ctrlfvuln.web.views import _safe_redirect

    with app.test_request_context("/"):
        assert _safe_redirect("https://evil.example/x", "/fallback") == "/fallback"
        assert _safe_redirect("/repo/10", "/fallback") == "/repo/10"
        assert _safe_redirect(None, "/fallback") == "/fallback"
        assert _safe_redirect("http://localhost/repo/10", "/fallback") == (
            "http://localhost/repo/10"
        )


def test_check_all_marks_every_file_in_a_repo(client, settings):
    response = client.post("/repo/11/check_all", data={"checked": "1"})
    assert response.status_code == 302
    assert _checked(settings.db_path, 102) == 1
    assert _checked(settings.db_path, 103) == 1


def test_check_all_on_a_missing_repo_is_404(client):
    assert client.post("/repo/99999/check_all", data={"checked": "1"}).status_code == 404


# --------------------------------------------------------------- JSON API


def test_api_toggles_a_file(client, settings):
    response = client.post("/api/files/100/checked", json={"checked": True})
    assert response.status_code == 200
    assert response.get_json()["checked"] is True
    assert _checked(settings.db_path, 100) == 1

    response = client.post("/api/files/100/checked", json={"checked": False})
    assert response.get_json()["checked"] is False
    assert _checked(settings.db_path, 100) == 0


def test_api_returns_a_fresh_summary(client):
    payload = client.post(
        "/api/files/100/checked", json={"checked": True, "project": "php-search"}
    ).get_json()
    assert payload["summary"] == {"files": 5, "checked": 2, "repos": 3}


def test_api_missing_file_is_404(client):
    response = client.post("/api/files/424242/checked", json={"checked": True})
    assert response.status_code == 404
    assert response.get_json()["error"]


def test_api_bulk_update(client, settings):
    response = client.post(
        "/api/files/checked",
        json={"ids": [100, 102, 103], "checked": True, "project": "php-search"},
    )
    assert response.status_code == 200
    assert response.get_json()["changed"] == 3
    assert all(_checked(settings.db_path, fid) == 1 for fid in (100, 102, 103))


def test_api_bulk_rejects_bad_input(client):
    assert client.post("/api/files/checked", json={"ids": "nope"}).status_code == 400
    assert client.post("/api/files/checked", json={"ids": ["x"]}).status_code == 400
    assert client.post(
        "/api/files/checked", json={"ids": list(range(1001))}
    ).status_code == 400


def test_api_bulk_with_no_ids_is_a_noop(client):
    assert client.post("/api/files/checked", json={"ids": []}).get_json()["changed"] == 0


def test_api_repo_toggle(client, settings):
    response = client.post("/api/repos/11/checked", json={"checked": True})
    assert response.get_json()["changed"] == 2
    assert _checked(settings.db_path, 102) == 1
    assert client.post("/api/repos/99999/checked", json={"checked": True}).status_code == 404


def test_api_accepts_string_booleans(client, settings):
    client.post("/api/files/100/checked", json={"checked": "false"})
    assert _checked(settings.db_path, 100) == 0


def test_api_404_returns_json_not_html(client):
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.is_json


def test_preview_of_a_missing_file_is_404(client):
    assert client.get("/api/files/999999/preview").status_code == 404


# ------------------------------------------------------------ cross-origin


def test_cross_origin_post_is_refused(client):
    response = client.post(
        "/api/files/100/checked",
        json={"checked": True},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_same_origin_post_is_allowed(client):
    response = client.post(
        "/api/files/100/checked",
        json={"checked": True},
        headers={"Origin": "http://localhost"},
    )
    assert response.status_code == 200


def test_cross_origin_get_is_allowed(client):
    response = client.get("/", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200


# ----------------------------------------------------------------- assets


def test_static_assets_are_served(client):
    assert client.get("/static/css/app.css").status_code == 200
    assert client.get("/static/js/app.js").status_code == 200
