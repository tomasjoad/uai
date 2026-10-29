import os
import sys

import pytest

from agent_core.sandbox import OutsideSandboxError, relative_in_root, resolve_in_root


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "sandbox"
    path.mkdir()
    return path


def test_relative_paths_resolve_inside_the_root(root):
    assert resolve_in_root(root, "notes/a.txt") == (root / "notes" / "a.txt").resolve()
    assert relative_in_root(root, "notes/../b.txt") == "b.txt"
    assert relative_in_root(root, ".") == "."


@pytest.mark.parametrize("raw", ["../escape.txt", "notes/../../escape.txt", ".."])
def test_dotdot_cannot_leave_the_root(root, raw):
    with pytest.raises(OutsideSandboxError):
        resolve_in_root(root, raw)


def test_absolute_paths_must_be_inside(root, tmp_path):
    assert resolve_in_root(root, str(root / "x.txt")) == (root / "x.txt").resolve()
    with pytest.raises(OutsideSandboxError):
        resolve_in_root(root, str(tmp_path / "outside.txt"))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths are case-insensitive")
def test_windows_comparison_ignores_case(root):
    assert relative_in_root(root, str(root).upper() + "\\x.txt").lower() == "x.txt"


def test_links_cannot_escape(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            pytest.skip("cannot create symlinks here")
        import _winapi  # junctions need no special privileges on Windows

        _winapi.CreateJunction(str(outside), str(link))
    with pytest.raises(OutsideSandboxError):
        resolve_in_root(root, "link/secret.txt")
