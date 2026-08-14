"""
Tests for the JP spawn-probe's install resolution.

find_game_dir decides whether the stock run.bat launch chain (XIGNCODE loader + game
exe) is present. Getting it wrong means either probing a folder that cannot launch, or
refusing a folder that can — so these build real directory layouts on disk (a genuine
install, a launcher-less one, a game-less one, a bogus path) and assert the verdict.
No mocks: the function only touches the filesystem, so the filesystem is what we give it.

Run:  python -m pytest test_jp_spawn_probe.py -v
   or python test_jp_spawn_probe.py     (built-in runner, no pytest needed)
"""

import importlib.util
import os
import tempfile

# frida/ has no __init__.py, so load the probe by path (same trick jp_shim.py uses).
_PROBE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frida", "jp_spawn_probe.py")
_spec = importlib.util.spec_from_file_location("jp_spawn_probe", _PROBE_PATH)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


def _make_install(root, files):
    """Create a fake game folder containing exactly `files`. Returns its path."""
    os.makedirs(root, exist_ok=True)
    for name in files:
        with open(os.path.join(root, name), "wb") as fh:
            fh.write(b"MZ")  # contents are irrelevant; only presence is checked
    return root


def test_complete_install_is_accepted():
    """Both halves of the run.bat chain present -> usable, no error."""
    with tempfile.TemporaryDirectory() as tmp:
        game = _make_install(os.path.join(tmp, "BlueArchive_JP"), [probe.LOADER, probe.EXE])
        found, err = probe.find_game_dir(game)
        assert err is None, f"expected no error, got {err!r}"
        assert found == game, f"expected {game!r}, got {found!r}"
        print(f"  accepted a complete install: {found}")


def test_missing_loader_is_rejected_and_named():
    """Game exe but no XIGNCODE loader: run.bat's chain is broken, so refuse — and say which
    file is missing, because 'not found' alone sends the user hunting."""
    with tempfile.TemporaryDirectory() as tmp:
        game = _make_install(os.path.join(tmp, "no_loader"), [probe.EXE])
        found, err = probe.find_game_dir(game)
        assert found is None, f"expected rejection, got {found!r}"
        assert probe.LOADER in err, f"error should name the missing loader, got {err!r}"
        print(f"  rejected + named the missing file: {err}")


def test_missing_exe_is_rejected_and_named():
    """Loader but no game exe — the mirror case."""
    with tempfile.TemporaryDirectory() as tmp:
        game = _make_install(os.path.join(tmp, "no_exe"), [probe.LOADER])
        found, err = probe.find_game_dir(game)
        assert found is None, f"expected rejection, got {found!r}"
        assert probe.EXE in err, f"error should name the missing exe, got {err!r}"
        print(f"  rejected + named the missing file: {err}")


def test_nonexistent_explicit_path_is_rejected_not_silently_defaulted():
    """An explicit path that does not exist must fail loudly. Falling through to the default
    install would silently probe a different game folder than the user asked for."""
    with tempfile.TemporaryDirectory() as tmp:
        bogus = os.path.join(tmp, "does_not_exist")
        found, err = probe.find_game_dir(bogus)
        assert found is None, f"expected rejection, got {found!r}"
        assert bogus in err, f"error should quote the path given, got {err!r}"
        assert "not a directory" in err, f"unexpected error text: {err!r}"
        print(f"  refused to fall through to the default: {err}")


def test_no_argument_falls_back_to_the_known_default():
    """With no argument the probe uses the known Yostar install path. On a machine where
    that install exists this must resolve to it; where it does not, the error must name it
    so the user knows which path to pass."""
    found, err = probe.find_game_dir(None)
    default_complete = all(os.path.exists(os.path.join(probe.DEFAULT_GAME_DIR, f))
                           for f in (probe.LOADER, probe.EXE))
    if default_complete:
        assert err is None, f"default install is complete but was rejected: {err!r}"
        assert found == probe.DEFAULT_GAME_DIR, f"expected the default dir, got {found!r}"
        print(f"  resolved the real install: {found}")
    else:
        assert found is None, f"default install is incomplete but was accepted: {found!r}"
        assert probe.DEFAULT_GAME_DIR in err, f"error should name the default, got {err!r}"
        print(f"  no install here; error names the default: {err}")


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            print(f"[RUN] {t.__name__}")
            t()
            print(f"[PASS] {t.__name__}\n")
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {t.__name__}: {exc}\n")
        except Exception as exc:  # noqa
            failed += 1
            print(f"[ERROR] {t.__name__}: {type(exc).__name__}: {exc}\n")
    total = len(tests)
    print(f"==== {total - failed}/{total} passed ====")
    return failed


if __name__ == "__main__":
    raise SystemExit(1 if _run_all() else 0)
