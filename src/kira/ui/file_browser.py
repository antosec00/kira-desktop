"""Kira's own chooser for folders and files: listing, places and plain file operations.

The chooser acts only on explicit user actions, like a file manager. Listings and
previews run off the GUI thread because Windows drives under WSL (/mnt/c) can
take seconds to list; a newer request always supersedes an older result.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import stat
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication

from kira.ui.attachments import _local_path
from kira.ui.presentation import tr
from kira.ui.qt_properties import qt_property

if TYPE_CHECKING:
    from kira.ui.controller import Workspace

Json = dict[str, Any]
MODES = frozenset({"folder", "open", "save"})
SORT_KEYS = frozenset({"name", "modified", "size"})
MAX_ENTRIES = 5000
MAX_HISTORY = 50
MAX_RECENT = 8
MAX_PURPOSES = 32
MAX_NAME_BYTES = 255
PREVIEW_BYTES = 16_384
PREVIEW_LINES = 80
MAX_IMAGE_PREVIEW_BYTES = 25 * 1024 * 1024
FOLDER_COUNT_LIMIT = 10_000

_KINDS = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".tif", ".tiff"},
    "code": {".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".qml", ".c", ".h", ".cc", ".cpp", ".hpp",
             ".rs", ".go", ".java", ".kt", ".rb", ".php", ".sh", ".bash", ".zsh", ".ps1", ".sql",
             ".lua", ".swift", ".cs", ".scala", ".r", ".pl", ".dart", ".vue", ".svelte"},
    "text": {".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json", ".jsonl", ".yaml",
             ".yml", ".toml", ".ini", ".cfg", ".conf", ".env", ".xml", ".html", ".htm", ".css",
             ".scss", ".tex", ".srt", ".gitignore", ".dockerignore"},
    "archive": {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".zst", ".deb", ".rpm",
                ".whl", ".jar", ".iso"},
    "document": {".pdf", ".doc", ".docx", ".odt", ".rtf", ".xls", ".xlsx", ".ods", ".ppt", ".pptx",
                 ".odp", ".epub", ".pages", ".numbers", ".key"},
    "audio": {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wma"},
    "video": {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".wmv"},
}
_IMAGE_PREVIEW = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"}
# Folders under /mnt/c/Users that are not a person's profile.
_WINDOWS_SYSTEM_PROFILES = {"public", "default", "default user", "all users", "defaultuser0",
                            "wsiaccount", "desktop.ini"}


def file_kind(name: str, is_dir: bool) -> str:
    if is_dir:
        return "folder"
    lowered = name.lower()
    suffix = os.path.splitext(lowered)[1] or (lowered if lowered.startswith(".") else "")
    return next((kind for kind, suffixes in _KINDS.items() if suffix in suffixes), "file")


def natural_key(name: str) -> list[Any]:
    """Order "file2" before "file10", ignoring case."""
    return [(0, int(part)) if part.isdigit() else (1, part.casefold())
            for part in re.split(r"(\d+)", name) if part]


def describe(entry: os.DirEntry[str]) -> Json:
    try:
        info = entry.stat()
        is_dir = stat.S_ISDIR(info.st_mode)
        size, modified, broken = (-1 if is_dir else info.st_size), int(info.st_mtime * 1000), False
    except OSError:
        # A dangling link or an entry the user may not inspect is still listed.
        is_dir, size, modified, broken = False, -1, 0, True
    try:
        link = entry.is_symlink()
    except OSError:
        link = False
    return {"name": entry.name, "path": entry.path, "isDir": is_dir, "size": size,
            "modified": modified, "kind": file_kind(entry.name, is_dir),
            "hidden": entry.name.startswith("."), "link": link, "broken": broken}


def list_directory(path: Path, limit: int = MAX_ENTRIES) -> tuple[list[Json], bool]:
    """At most limit entries of one folder; the flag says that more exist."""
    rows: list[Json] = []
    with os.scandir(path) as iterator:
        for entry in iterator:
            if len(rows) >= limit:
                return rows, True
            rows.append(describe(entry))
    return rows, False


def sort_entries(rows: list[Json], key: str, descending: bool) -> list[Json]:
    """Folders first; folders follow the date order too, otherwise their name."""
    def order(row: Json) -> Any:
        if key == "modified":
            return row["modified"]
        if key == "size" and not row["isDir"]:
            return row["size"]
        return natural_key(row["name"])

    folders = sorted((row for row in rows if row["isDir"]), key=order,
                     reverse=descending and key != "size")
    files = sorted((row for row in rows if not row["isDir"]), key=order, reverse=descending)
    return folders + files


def name_problem(name: str) -> str:
    """Why a typed file or folder name cannot be used; empty when it can."""
    if not name.strip():
        return tr("Scrivi un nome.")
    if name in {".", ".."} or "/" in name or "\0" in name:
        return tr("Il nome non può contenere / né essere . o ..")
    if len(name.encode("utf-8", "surrogateescape")) > MAX_NAME_BYTES:
        return tr("Il nome è troppo lungo.")
    return ""


def operation_problem(error: OSError) -> str:
    if isinstance(error, FileExistsError):
        return tr("Esiste già un elemento con questo nome.")
    if isinstance(error, PermissionError):
        return tr("Non hai il permesso di modificare questa cartella.")
    if isinstance(error, FileNotFoundError):
        return tr("L'elemento non esiste più.")
    return tr("Operazione non riuscita: {error}", error=error.strerror or type(error).__name__)


def build_preview(path: Path) -> Json:
    info = path.stat()
    result: Json = {"path": str(path), "name": path.name or str(path),
                    "modified": int(info.st_mtime * 1000), "permissions": stat.filemode(info.st_mode)}
    if stat.S_ISDIR(info.st_mode):
        count, more = 0, False
        try:
            with os.scandir(path) as iterator:
                for _ in iterator:
                    count += 1
                    if count >= FOLDER_COUNT_LIMIT:
                        more = True
                        break
        except OSError:
            result["unreadable"] = True
        return {**result, "kind": "folder", "items": count, "itemsTruncated": more}
    kind = file_kind(path.name, False)
    result.update(kind=kind, size=info.st_size)
    if path.suffix.lower() in _IMAGE_PREVIEW and info.st_size <= MAX_IMAGE_PREVIEW_BYTES:
        result["imageUrl"] = QUrl.fromLocalFile(str(path)).toString()
        return result
    if kind in {"image", "archive", "audio", "video", "document"}:
        return result
    with path.open("rb") as handle:
        data = handle.read(PREVIEW_BYTES)
    if b"\0" in data:
        return result
    text = data.decode("utf-8", "replace")
    lines = text.splitlines()
    result["text"] = "\n".join(lines[:PREVIEW_LINES])
    result["textTruncated"] = len(lines) > PREVIEW_LINES or info.st_size > PREVIEW_BYTES
    return result


def _same_entry(first: Path, second: Path) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def _user_dirs(home: Path) -> dict[str, Path]:
    """The XDG user folders named in ~/.config/user-dirs.dirs, when present."""
    found: dict[str, Path] = {}
    try:
        lines = (home / ".config" / "user-dirs.dirs").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return found
    for line in lines:
        match = re.fullmatch(r'\s*XDG_(DESKTOP|DOCUMENTS|DOWNLOAD)_DIR="([^"]*)"\s*', line)
        if match:
            found[match[1]] = Path(match[2].replace("$HOME", str(home)))
    return found


class FileBrowser(QObject):
    """One chooser session at a time; the dialogs that use it are modal."""

    changed = Signal()
    placesChanged = Signal()
    previewChanged = Signal()
    _finished = Signal(object)

    def __init__(self, workspace: Workspace, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._workspace = workspace
        self._wsl = bool(os.environ.get("WSL_DISTRO_NAME") or "microsoft" in os.uname().release.lower())
        self._distro = os.environ.get("WSL_DISTRO_NAME")
        self._mode = "folder"
        self._purpose = "general"
        self._path = ""
        self._all: list[Json] = []
        self._entries: list[Json] = []
        self._loading = False
        self._error = ""
        self._truncated = False
        self._writable = False
        self._filter = ""
        self._patterns: list[str] = []
        self._select = ""
        self._history: list[str] = []
        self._position = -1
        self._generation = 0
        self._preview_generation = 0
        self._preview: Json = {}
        self._windows_places: list[Json] | None = None
        self._place_list: list[Json] = []
        state = self._read_state()
        self._last: dict[str, str] = state["last"]
        self._recent: list[str] = state["recent"]
        self._show_hidden: bool = state["show_hidden"]
        self._sort_key: str = state["sort_key"]
        self._descending: bool = state["descending"]
        self._finished.connect(self._deliver)

    # Persistent choices: last folder per purpose, recent folders and view options.

    @property
    def _state_path(self) -> Path:
        return self._workspace.paths.config / "file-browser.json"

    def _read_state(self) -> Json:
        state: Json = {"last": {}, "recent": [], "show_hidden": False, "sort_key": "name",
                       "descending": False}
        try:
            value = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            return state
        if not isinstance(value, dict):
            return state
        last = value.get("last")
        if isinstance(last, dict):
            state["last"] = {key: item for key, item in list(last.items())[:MAX_PURPOSES]
                             if isinstance(key, str) and isinstance(item, str)}
        recent = value.get("recent")
        if isinstance(recent, list):
            state["recent"] = [item for item in recent if isinstance(item, str)][:MAX_RECENT]
        if isinstance(value.get("show_hidden"), bool):
            state["show_hidden"] = value["show_hidden"]
        if value.get("sort_key") in SORT_KEYS:
            state["sort_key"] = value["sort_key"]
        if isinstance(value.get("descending"), bool):
            state["descending"] = value["descending"]
        return state

    def _write_state(self) -> None:
        payload = json.dumps({"last": self._last, "recent": self._recent,
                              "show_hidden": self._show_hidden, "sort_key": self._sort_key,
                              "descending": self._descending}, ensure_ascii=False)
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self._state_path.with_suffix(".tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(temporary, self._state_path)
        except OSError:
            # Remembering folders is a convenience; choosing still works.
            pass

    # Background work: newer requests supersede older results.

    def _run(self, kind: str, generation: int, work: Callable[[], Any]) -> None:
        def run() -> None:
            try:
                outcome: tuple[bool, Any] = (True, work())
            except Exception as error:  # reported to the user, never raised in a thread
                outcome = (False, error)
            try:
                self._finished.emit((kind, generation, outcome))
            except RuntimeError:
                pass  # the chooser was destroyed while the disk answered

        threading.Thread(target=run, name=f"kira-file-browser-{kind}", daemon=True).start()

    @Slot(object)
    def _deliver(self, message: tuple[str, int, tuple[bool, Any]]) -> None:
        kind, generation, (ok, value) = message
        if kind == "list":
            if generation != self._generation:
                return
            self._loading = False
            if ok:
                rows, truncated, writable = value
                self._all, self._truncated, self._writable, self._error = rows, truncated, writable, ""
            else:
                self._all, self._truncated, self._writable = [], False, False
                self._error = self._listing_problem(value)
            self._refresh_entries()
        elif kind == "preview" and generation == self._preview_generation:
            self._preview = value if ok else {"path": "", "error": tr("Anteprima non disponibile.")}
            self.previewChanged.emit()

    @staticmethod
    def _listing_problem(error: Exception) -> str:
        if isinstance(error, PermissionError):
            return tr("Non hai il permesso di aprire questa cartella.")
        if isinstance(error, (FileNotFoundError, NotADirectoryError)):
            return tr("La cartella non esiste più.")
        if isinstance(error, OSError):
            return tr("Impossibile leggere la cartella: {error}",
                      error=error.strerror or type(error).__name__)
        return tr("Impossibile leggere la cartella.")

    def _refresh_entries(self) -> None:
        needle = self._filter.casefold()
        rows = [row for row in self._all
                if (self._show_hidden or not row["hidden"])
                and (not needle or needle in row["name"].casefold())
                and (row["isDir"] or not self._patterns
                     or any(fnmatch.fnmatch(row["name"].casefold(), pattern) for pattern in self._patterns))]
        self._entries = sort_entries(rows, self._sort_key, self._descending)
        self.changed.emit()

    def _load(self, path: str, *, record: bool) -> None:
        if record:
            del self._history[self._position + 1:]
            if not self._history or self._history[-1] != path:
                self._history.append(path)
                self._history = self._history[-MAX_HISTORY:]
            self._position = len(self._history) - 1
        self._path = path
        self._loading, self._error, self._all, self._entries = True, "", [], []
        self._truncated = False
        self._generation += 1
        self._preview_generation += 1
        self._preview = {}
        self.previewChanged.emit()
        self.changed.emit()
        target = Path(path)

        def listing() -> tuple[list[Json], bool, bool]:
            rows, truncated = list_directory(target)
            return rows, truncated, os.access(target, os.W_OK)

        self._run("list", self._generation, listing)

    # Properties read by the dialog.

    @qt_property(str, notify=changed)
    def path(self) -> str:
        return self._path

    @qt_property("QVariantList", notify=changed)
    def entries(self) -> list[Json]:
        return self._entries

    @qt_property(bool, notify=changed)
    def loading(self) -> bool:
        return self._loading

    @qt_property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @qt_property(bool, notify=changed)
    def truncated(self) -> bool:
        return self._truncated

    @qt_property(bool, notify=changed)
    def writable(self) -> bool:
        return self._writable

    @qt_property(int, notify=changed)
    def hiddenCount(self) -> int:
        return 0 if self._show_hidden else sum(1 for row in self._all if row["hidden"])

    @qt_property(bool, notify=changed)
    def showHidden(self) -> bool:
        return self._show_hidden

    @qt_property(str, notify=changed)
    def sortKey(self) -> str:
        return self._sort_key

    @qt_property(bool, notify=changed)
    def sortDescending(self) -> bool:
        return self._descending

    @qt_property(str, notify=changed)
    def filterText(self) -> str:
        return self._filter

    @qt_property(str, notify=changed)
    def selectName(self) -> str:
        return self._select

    @qt_property(bool, notify=changed)
    def canGoBack(self) -> bool:
        return self._position > 0

    @qt_property(bool, notify=changed)
    def canGoForward(self) -> bool:
        return 0 <= self._position < len(self._history) - 1

    @qt_property(bool, notify=changed)
    def canGoUp(self) -> bool:
        return bool(self._path) and Path(self._path).parent != Path(self._path)

    @qt_property("QVariantList", notify=changed)
    def crumbs(self) -> list[Json]:
        if not self._path:
            return []
        path = Path(self._path)
        parts = [*reversed(path.parents), path]
        return [{"label": part.name or "/", "path": str(part)} for part in parts]

    @qt_property("QVariantList", notify=placesChanged)
    def places(self) -> list[Json]:
        return self._place_list

    @qt_property("QVariantMap", notify=previewChanged)
    def preview(self) -> Json:
        return self._preview

    def _places(self) -> list[Json]:
        home = Path.home()
        result: list[Json] = []
        seen: set[str] = set()

        def add(group: str, label: str, path: Path, icon: str) -> None:
            key = str(path)
            if key not in seen and path.is_dir():
                seen.add(key)
                result.append({"group": group, "label": label, "path": key, "icon": icon})

        favourites = tr("Preferiti")
        add(favourites, tr("Home"), home, "home")
        user_dirs = _user_dirs(home)
        for key, label, icon, fallback in (("DESKTOP", tr("Scrivania"), "desktop", "Desktop"),
                                           ("DOCUMENTS", tr("Documenti"), "file", "Documents"),
                                           ("DOWNLOAD", tr("Scaricati"), "download", "Downloads")):
            add(favourites, label, user_dirs.get(key, home / fallback), icon)
        for project in self._workspace.projects[:8]:
            root = project.get("root") if isinstance(project, dict) else None
            if isinstance(root, str) and root:
                add(tr("Progetti di Kira"), str(project.get("name") or Path(root).name), Path(root), "projects")
        for recent in self._recent:
            add(tr("Recenti"), Path(recent).name or recent, Path(recent), "resume")
        if self._wsl:
            for place in self._windows():
                add(tr("Windows"), place["label"], Path(place["path"]), place["icon"])
        add(tr("Sistema"), tr("Radice"), Path("/"), "drive")
        return result

    def _windows(self) -> list[Json]:
        if self._windows_places is None:
            places: list[Json] = []
            for letter in "cdefgh":
                drive = Path("/mnt") / letter
                if drive.is_dir():
                    places.append({"label": tr("Disco {letter}:", letter=letter.upper()),
                                   "path": str(drive), "icon": "drive"})
            try:
                profiles = [entry for entry in os.scandir("/mnt/c/Users")
                            if entry.is_dir() and entry.name.casefold() not in _WINDOWS_SYSTEM_PROFILES]
            except OSError:
                profiles = []
            if len(profiles) == 1:
                places.insert(0, {"label": tr("Utente Windows · {name}", name=profiles[0].name),
                                  "path": profiles[0].path, "icon": "home"})
            self._windows_places = places
        return self._windows_places

    # Navigation.

    @Slot(str, str, str)
    def begin(self, mode: str, purpose: str, start: str) -> None:
        self._mode = mode if mode in MODES else "folder"
        self._purpose = purpose[:64] or "general"
        self._filter, self._select = "", ""
        self._place_list = self._places()
        self.placesChanged.emit()
        candidates = [start, self._last.get(self._purpose, ""), str(Path.home())]
        for candidate in candidates:
            if candidate and not self.navigate(candidate):
                return
        self._load("/", record=True)

    @Slot(str, result=str)
    def navigate(self, text: str) -> str:
        """Open a folder, or a file's folder with the file selected; empty when it worked."""
        value = text.strip()
        if not value:
            return tr("Scrivi un percorso.")
        try:
            if value == "~" or value.startswith("~/"):
                path = Path(value).expanduser()
            else:
                local = _local_path(value, self._distro, self._wsl)
                path = local if local is not None else Path(self._path or Path.home()) / value
        except ValueError as error:
            return str(error)
        path = Path(os.path.normpath(path))
        try:
            if path.is_dir():
                self._select = ""
                self._load(str(path), record=True)
                return ""
            if path.exists():
                self._select = path.name
                self._load(str(path.parent), record=True)
                return ""
        except OSError as error:
            return self._listing_problem(error)
        return tr("Percorso non trovato: {path}", path=str(path))

    @Slot()
    def goBack(self) -> None:
        if self._position > 0:
            self._position -= 1
            self._select = Path(self._path).name
            self._load(self._history[self._position], record=False)

    @Slot()
    def goForward(self) -> None:
        if 0 <= self._position < len(self._history) - 1:
            self._position += 1
            self._select = ""
            self._load(self._history[self._position], record=False)

    @Slot()
    def goUp(self) -> None:
        if self._path and Path(self._path).parent != Path(self._path):
            self._select = Path(self._path).name
            self._load(str(Path(self._path).parent), record=True)

    @Slot()
    def refresh(self) -> None:
        if self._path:
            self._load(self._path, record=False)

    @Slot(bool)
    def setShowHidden(self, visible: bool) -> None:
        self._show_hidden = bool(visible)
        self._write_state()
        self._refresh_entries()

    @Slot(str)
    def setSort(self, key: str) -> None:
        if key not in SORT_KEYS:
            return
        self._descending = not self._descending if key == self._sort_key else key != "name"
        self._sort_key = key
        self._write_state()
        self._refresh_entries()

    @Slot(str)
    def setFilter(self, text: str) -> None:
        self._filter = text[:200]
        self._refresh_entries()

    @Slot("QVariantList")
    def setPatterns(self, patterns: list[Any]) -> None:
        cleaned = [str(item).strip().casefold() for item in patterns if str(item).strip()][:16]
        self._patterns = [] if "*" in cleaned else cleaned
        self._refresh_entries()

    @Slot(str)
    def requestPreview(self, path: str) -> None:
        self._preview_generation += 1
        if not path:
            self._preview = {}
            self.previewChanged.emit()
            return
        target = Path(path)
        self._run("preview", self._preview_generation, lambda: build_preview(target))

    # Plain operations requested by the user.

    @Slot(str, result=str)
    def createFolder(self, name: str) -> str:
        problem = name_problem(name)
        if problem:
            return problem
        try:
            (Path(self._path) / name).mkdir()
        except OSError as error:
            return operation_problem(error)
        self._select = name
        self.refresh()
        return ""

    @Slot(str, str, result=str)
    def rename(self, path: str, name: str) -> str:
        problem = name_problem(name)
        if problem:
            return problem
        source = Path(path)
        target = source.with_name(name)
        if target == source:
            return ""
        # Renaming never replaces another entry, even where the system would allow it;
        # on a case-insensitive Windows drive the "other" entry may be the same one.
        if os.path.lexists(target) and not _same_entry(source, target):
            return tr("Esiste già un elemento con questo nome.")
        try:
            source.rename(target)
        except OSError as error:
            return operation_problem(error)
        self._select = name
        self.refresh()
        return ""

    @Slot(str)
    def copyText(self, text: str) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)

    @Slot(str)
    def openExternally(self, path: str) -> None:
        if path and os.path.exists(path):
            self._workspace._open_local_path(path)

    @Slot(str, str, result="QVariantMap")
    def saveTarget(self, name: str, suffix: str) -> Json:
        problem = name_problem(name)
        if problem:
            return {"error": problem}
        clean_suffix = suffix.strip().lstrip(".")
        if clean_suffix and not Path(name).suffix:
            name = f"{name}.{clean_suffix}"
        folder = Path(self._path)
        if not os.access(folder, os.W_OK):
            return {"error": tr("Non hai il permesso di scrivere in questa cartella.")}
        target = folder / name
        if target.is_dir():
            return {"error": tr("Esiste una cartella con questo nome.")}
        return {"path": str(target), "name": name, "url": QUrl.fromLocalFile(str(target)).toString(),
                "exists": target.exists(), "error": ""}

    @Slot(str, result=str)
    def urlFor(self, path: str) -> str:
        return QUrl.fromLocalFile(path).toString()

    @Slot("QVariantList", result="QVariantList")
    def urlsFor(self, paths: list[Any]) -> list[str]:
        return [QUrl.fromLocalFile(str(path)).toString() for path in paths]

    @Slot(str)
    def remember(self, folder: str) -> None:
        """Called when a choice is confirmed: the next chooser starts there."""
        if not folder:
            return
        self._last[self._purpose] = folder
        if len(self._last) > MAX_PURPOSES:
            self._last.pop(next(iter(self._last)))
        self._recent = [folder, *(item for item in self._recent if item != folder)][:MAX_RECENT]
        self._write_state()
