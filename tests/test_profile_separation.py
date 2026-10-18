"""Actual stopped-store copy/alias migration, rollback and writer exclusion."""

import fcntl
import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("separate_profile", Path(__file__).parents[1] / "scripts/separate_profile.py")
assert spec is not None and spec.loader is not None
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.fixture
def activation(monkeypatch):
    monkeypatch.setitem(sys.modules, "separate_profile", migration)
    spec = importlib.util.spec_from_file_location(
        "activate_standalone", Path(__file__).parents[1] / "scripts/activate_standalone.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("missing", ["source-root", "source-profile", "profile", "launcher"])
def test_activation_requires_all_explicit_installation_paths(activation, tmp_path, missing, capsys):
    argv = []
    for option in ("source-root", "source-profile", "profile", "launcher"):
        if option != missing:
            argv.extend(("--" + option, str(tmp_path / option)))
    with pytest.raises(SystemExit) as error:
        activation.parse_args(argv)
    assert error.value.code == 2
    assert "--" + missing in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


async def test_activation_readiness_uses_explicit_profile_without_changes(
    activation, profile, tmp_path, monkeypatch, capsys
):
    source, destination, _archive = profile
    with sqlite3.connect(source / "data/kira.sqlite3") as db:
        db.executescript("""
            CREATE TABLE team_runs (state TEXT);
            CREATE TABLE sessions (id TEXT, project_id TEXT, model TEXT, settings TEXT);
            CREATE TABLE runs (id TEXT, session_id TEXT, created_at TEXT);
            CREATE TABLE team_run_links (run_id TEXT);
        """)
    original = migration.file_manifest(source)
    requests = []

    class ReadinessClient:
        def __init__(self, paths):
            assert paths.db == source / "data/kira.sqlite3"

        async def connect(self):
            requests.append("connect")

        async def request(self, method):
            requests.append(method)
            assert method == "system.diagnostics"
            return {"app_version": activation.__version__, "service": {"active_run_count": 0}}

        async def close(self):
            requests.append("close")

    monkeypatch.setattr(activation, "Client", ReadinessClient)
    monkeypatch.setenv("KIRA_HOME", str(tmp_path / "unrelated-profile"))
    args = activation.parse_args([
        "--source-root", str(tmp_path / "old-checkout"),
        "--source-profile", str(source),
        "--profile", str(destination),
        "--launcher", str(tmp_path / "custom-launcher"),
    ])
    assert await activation.activate(args) == 0
    assert requests == ["connect", "system.diagnostics", "close"]
    assert json.loads(capsys.readouterr().out) == {
        "status": "ready", "source": str(source), "destination": str(destination),
        "apply_required": True,
    }
    assert migration.file_manifest(source) == original
    assert not destination.exists() and not (tmp_path / "unrelated-profile").exists()


def test_activation_relaunch_uses_installed_application_without_private_helpers(
    activation, tmp_path, monkeypatch
):
    python = tmp_path / "checkout/.venv/bin/python"
    cwd = tmp_path / "checkout"
    env = {"KIRA_HOME": str(tmp_path / "explicit-profile")}
    launches = []

    def popen(argv, **options):
        launches.append((argv, options))
        return SimpleNamespace(pid=42)

    monkeypatch.setattr(activation.subprocess, "Popen", popen)
    assert activation.launch(python, cwd, env, tmp_path / "gui.log") == 42
    assert len(launches) == 1
    argv, options = launches[0]
    assert argv == [str(python), "-I", "-m", "kira"]
    assert options["cwd"] == cwd and options["env"] == env
    assert options["start_new_session"] is True


@pytest.fixture
def process_tree(activation, tmp_path, monkeypatch):
    proc = tmp_path / "synthetic-proc"
    proc.mkdir()
    monkeypatch.setattr(activation, "PROC", proc)
    root = tmp_path / "checkout"
    home = tmp_path / "profile"

    def add(pid, argv, profile=home, start=123):
        directory = proc / str(pid)
        directory.mkdir()
        command = b"\0".join(os.fsencode(arg) for arg in argv) + b"\0"
        (directory / "cmdline").write_bytes(command)
        (directory / "environ").write_bytes(b"KIRA_HOME=" + os.fsencode(profile) + b"\0")
        (directory / "stat").write_text(
            f"{pid} (synthetic fixture) " + " ".join(["S", *(["0"] * 18), str(start)])
        )
        return directory

    return root, home, add


@pytest.mark.parametrize("interpreter", ["python", "python3", "python3.12", "python3.14"])
def test_activation_recognizes_owned_module_and_console_guis(
    activation, process_tree, interpreter
):
    root, home, add = process_tree
    python = root / ".venv/bin" / interpreter
    console = root / ".venv/bin/kira-desktop"
    add(101, [python, "-I", "-m", "kira.service.server"])
    add(102, [python, console])
    add(103, [python, "-I", "-m", "kira"])
    add(104, [console])
    add(105, [python, "-I", root / "build/migration/open_updated_desktop.py"])
    guis, service = activation.processes(root, home)
    assert {gui["pid"] for gui in guis} == {102, 103, 104, 105}
    assert service["pid"] == 101 and service["identity"][1] == 123


def test_activation_excludes_other_profiles_checkouts_and_command_arguments(
    activation, process_tree
):
    root, home, add = process_tree
    python = root / ".venv/bin/python3.14"
    console = root / ".venv/bin/kira-desktop"
    add(101, [python, "-m", "kira.service.server"])
    add(102, [python, console], profile=home.with_name("unrelated-profile"))
    unrelated = [
        [root.parent / "other-checkout/.venv/bin/python3.14", "-m", "kira"],
        [root / ".venv/bin/python3.14-config", "-m", "kira"],
        [python, "unrelated.py", "-m", "kira"],
        [python, "unrelated.py", console],
        [python, "-c", "print('synthetic')", console],
        [python, console, "doctor"],
        [console, "rpc", "system.diagnostics"],
        [python, root.parent / "other-checkout/.venv/bin/kira-desktop"],
        [python, root.parent / "other-checkout/build/open_updated_desktop.py"],
        [python, root / "build/../outside/open_updated_desktop.py"],
    ]
    for pid, argv in enumerate(unrelated, 110):
        add(pid, argv)
    guis, service = activation.processes(root, home)
    assert guis == [] and service["pid"] == 101


@pytest.mark.parametrize("service_count", [0, 2])
def test_activation_requires_exactly_one_owned_service(activation, process_tree, service_count):
    root, home, add = process_tree
    for pid in range(101, 101 + service_count):
        add(pid, [root / ".venv/bin/python3.14", "-m", "kira.service.server"])
    with pytest.raises(RuntimeError, match="exactly one original service"):
        activation.processes(root, home)


def test_activation_does_not_signal_reused_process_identity(
    activation, process_tree, monkeypatch
):
    root, home, add = process_tree
    directory = add(101, [root / ".venv/bin/python3.14", "-m", "kira.service.server"])
    _guis, service = activation.processes(root, home)
    (directory / "cmdline").write_bytes(b"unrelated-process\0")
    closed = []
    monkeypatch.setattr(activation, "os", SimpleNamespace(pidfd_open=lambda pid: 7, close=closed.append))

    def forbidden_signal(*args):
        pytest.fail("A process with a changed identity must never be signalled")

    monkeypatch.setattr(activation, "signal", SimpleNamespace(pidfd_send_signal=forbidden_signal, SIGTERM=15))
    with pytest.raises(RuntimeError, match="Process identity changed"):
        activation.terminate_exact(service)
    assert closed == [7]


@pytest.fixture
def profile(tmp_path):
    source = tmp_path / "original"
    for name in ("config", "data/artifacts", "data/attachments", "state", "runtime"):
        (source / name).mkdir(parents=True, mode=0o700)
    (source / "config/ui.json").write_text('{"density":"compact"}')
    (source / "data/attachments/clipboard-fixture.png").write_bytes(b"synthetic preserved bytes")
    (source / "data/artifacts/note.txt").write_text("Explicit artifact")
    (source / "state/service.log").write_text("Fixture log")
    (source / "data/kira.lock").touch(mode=0o600)
    db = sqlite3.connect(source / "data/kira.sqlite3")
    db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, path TEXT, value TEXT)")
    db.execute("INSERT INTO notes VALUES (1, ?, 'preserved')", (str(source / "data/attachments/clipboard-fixture.png"),))
    db.commit()
    db.close()
    for entry in source.rglob("*"):
        entry.chmod(0o700 if entry.is_dir() else 0o600)
    return source, tmp_path / "destination", tmp_path / "archive"


def assert_locked(path):
    with path.open("rb") as stream:
        with pytest.raises(BlockingIOError):
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_exact_profile_copy_alias_preserves_embedded_paths_and_single_writer(profile):
    source, destination, archive = profile
    original = migration.file_manifest(source)
    with migration.ProfileSeparation(source, destination, archive) as move:
        assert_locked(source / "data/kira.lock")
        assert_locked(destination / "data/kira.lock")
        move.switch()
        assert source.is_symlink() and archive.is_dir()
        assert os.path.samefile(source / "data/kira.lock", destination / "data/kira.lock")
        assert os.path.samefile(source / "data/kira.sqlite3", destination / "data/kira.sqlite3")
        assert_locked(source / "data/kira.lock")
        db = sqlite3.connect((destination / "data/kira.sqlite3").as_uri() + "?mode=ro&immutable=1", uri=True)
        saved_path = db.execute("SELECT path FROM notes").fetchone()[0]
        db.close()
        assert Path(saved_path).read_bytes() == b"synthetic preserved bytes"
        assert migration.file_manifest(destination) == original
        assert migration.table_fingerprints(destination / "data/kira.sqlite3") == move.report["tables"]
    with (source / "data/kira.lock").open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert destination.stat().st_mode & 0o777 == 0o700
    assert migration.file_manifest(archive) == original


def test_active_writer_prevents_any_destination_creation(profile):
    source, destination, archive = profile
    with (source / "data/kira.lock").open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            with migration.ProfileSeparation(source, destination, archive):
                pytest.fail("Unexpected acquisition")
    assert not destination.exists() and not archive.exists() and not source.is_symlink()


@pytest.mark.parametrize("name", ["destination", "archive"])
def test_existing_destination_is_never_overwritten(profile, name):
    source, destination, archive = profile
    target = destination if name == "destination" else archive
    target.mkdir()
    (target / "keep.txt").write_text("Preserve existing contents")
    with pytest.raises(FileExistsError):
        with migration.ProfileSeparation(source, destination, archive):
            pytest.fail("Unexpected overwrite")
    assert (target / "keep.txt").read_text() == "Preserve existing contents"


def test_cutover_exception_rolls_back_before_start(profile):
    source, destination, archive = profile
    original = migration.file_manifest(source)
    with pytest.raises(RuntimeError, match="launcher failed"):
        with migration.ProfileSeparation(source, destination, archive) as move:
            move.switch()
            raise RuntimeError("launcher failed")
    assert source.is_dir() and not source.is_symlink() and not archive.exists()
    assert migration.file_manifest(source) == original
    assert destination.is_dir()  # Retained private copy for explicit recovery.


def test_copy_failure_preserves_original(profile, monkeypatch):
    source, destination, archive = profile
    original = migration.file_manifest(source)
    monkeypatch.setattr(migration.shutil, "copytree", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("copy failed")))
    with pytest.raises(OSError, match="copy failed"):
        with migration.ProfileSeparation(source, destination, archive):
            pytest.fail("Unexpected copy success")
    assert not source.is_symlink() and not archive.exists()
    assert migration.file_manifest(source) == original
    with (source / "data/kira.lock").open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_private_subdirectory_symlinks_are_rejected(profile):
    source, destination, archive = profile
    (source / "runtime").rmdir()
    (source / "runtime").symlink_to(source / "state", target_is_directory=True)
    with pytest.raises(ValueError, match="real directories"):
        with migration.ProfileSeparation(source, destination, archive):
            pytest.fail("Unexpected linked directory")
    assert not destination.exists()


def test_nonempty_wal_prevents_immutable_read_data_loss(profile):
    source, destination, archive = profile
    (source / "data/kira.sqlite3-wal").write_bytes(b"must not ignore this")
    with pytest.raises(RuntimeError, match="WAL"):
        with migration.ProfileSeparation(source, destination, archive):
            pytest.fail("Ignored outstanding WAL")
    assert not destination.exists()


def test_recreated_source_preserves_archive_and_destination(profile, monkeypatch):
    source, destination, archive = profile
    original = migration.file_manifest(source)
    real_symlink = Path.symlink_to

    def concurrent_launch(path, target, target_is_directory=False):
        if path == source:
            source.mkdir()
            (source / 'concurrent-marker').write_text('unrelated new startup')
            raise FileExistsError(source)
        return real_symlink(path, target, target_is_directory=target_is_directory)

    with migration.ProfileSeparation(source, destination, archive) as move:
        monkeypatch.setattr(Path, 'symlink_to', concurrent_launch)
        with pytest.raises(RuntimeError, match='recreated during cutover'):
            move.switch()
        assert migration.file_manifest(archive) == original
        assert migration.file_manifest(destination) == original
        assert (source / 'concurrent-marker').read_text() == 'unrelated new startup'
        assert_locked(archive / 'data/kira.lock')
        assert_locked(destination / 'data/kira.lock')
