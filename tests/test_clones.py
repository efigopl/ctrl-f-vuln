"""Clone path construction and editor launching."""

from __future__ import annotations

import pytest

from ctrlfvuln.clones import (
    CloneError,
    clone_dir,
    find_existing_clone,
    legacy_clone_dir,
    open_in_editor,
    safe_component,
    sanitize_component,
    split_full_name,
)


def test_clone_dirs_are_namespaced_by_owner(tmp_path):
    """Regression: both repos used to resolve to <project>/utils."""
    alice = clone_dir(tmp_path, "proj", "alice/utils")
    bob = clone_dir(tmp_path, "proj", "bob/utils")
    assert alice != bob
    assert alice == tmp_path / "proj" / "alice" / "utils"
    assert bob == tmp_path / "proj" / "bob" / "utils"


def test_legacy_layout_collides_as_it_used_to(tmp_path):
    """Documents why the layout changed."""
    assert legacy_clone_dir(tmp_path, "proj", "alice/utils") == legacy_clone_dir(
        tmp_path, "proj", "bob/utils"
    )


def test_existing_clone_found_in_new_layout(tmp_path):
    target = clone_dir(tmp_path, "proj", "alice/shop")
    (target / ".git").mkdir(parents=True)
    assert find_existing_clone(tmp_path, "proj", "alice/shop") == target


def test_existing_clone_found_in_legacy_layout(tmp_path):
    """A clone made by the previous version is still detected."""
    legacy = legacy_clone_dir(tmp_path, "proj", "alice/shop")
    (legacy / ".git").mkdir(parents=True)
    assert find_existing_clone(tmp_path, "proj", "alice/shop") == legacy


def test_missing_clone_returns_none(tmp_path):
    assert find_existing_clone(tmp_path, "proj", "alice/shop") is None


@pytest.mark.parametrize("component", ["..", ".", "", "a/b", "a\\b", "with space", "x;rm"])
def test_unsafe_components_are_rejected(component):
    with pytest.raises(CloneError):
        safe_component(component)


def test_path_traversal_in_repo_name_is_rejected(tmp_path):
    with pytest.raises(CloneError):
        clone_dir(tmp_path, "proj", "../../etc/passwd")


def test_malformed_full_name_is_rejected():
    with pytest.raises(CloneError):
        split_full_name("no-slash")


def test_project_names_are_sanitized_not_rejected():
    assert sanitize_component("my project/../x") == "my_project_.._x"
    assert sanitize_component("") == "unnamed"
    assert sanitize_component("..") == "unnamed"


def test_open_in_editor_without_a_configured_editor(tmp_path):
    with pytest.raises(CloneError, match="No editor configured"):
        open_in_editor(None, tmp_path)


def test_open_in_editor_with_a_missing_command(tmp_path):
    with pytest.raises(CloneError, match="not found"):
        open_in_editor("definitely-not-an-editor-binary", tmp_path)
