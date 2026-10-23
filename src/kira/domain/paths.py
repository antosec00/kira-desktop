"""Private per-user XDG paths, or one KIRA_HOME directory holding a whole profile."""

import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    config: Path
    data: Path
    state: Path
    runtime: Path

    @classmethod
    def from_env(cls) -> "Paths":
        override = os.environ.get("KIRA_HOME")
        if override:
            base = Path(override).expanduser().absolute()
            return cls(*(base / part for part in ("config", "data", "state", "runtime")))
        home = Path.home()

        def xdg(name: str, default: Path) -> Path:
            # The XDG specification treats a variable set to an empty value as unset.
            return Path(os.environ.get(name) or default)

        state = xdg("XDG_STATE_HOME", home / ".local/state") / "kira"
        return cls(
            xdg("XDG_CONFIG_HOME", home / ".config") / "kira",
            xdg("XDG_DATA_HOME", home / ".local/share") / "kira",
            state,
            Path(os.environ["XDG_RUNTIME_DIR"]) / "kira"
            if os.environ.get("XDG_RUNTIME_DIR") else state / "runtime",
        )

    @property
    def socket(self) -> Path:
        return self.runtime / "service.sock"

    @property
    def db(self) -> Path:
        return self.data / "kira.sqlite3"

    @property
    def artifacts(self) -> Path:
        return self.data / "artifacts"

    def ensure(self) -> None:
        for path in (self.config, self.data, self.state, self.runtime, self.artifacts):
            if not path.is_absolute():
                raise ValueError("Kira XDG paths must be absolute")
            if path.is_symlink():
                raise ValueError(f"Private Kira directory must not be a symlink: {path}")
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            info = path.lstat()
            if info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode):
                raise PermissionError(f"Kira directory must be owned by this user: {path}")
            path.chmod(0o700)
        if len(os.fsencode(self.socket)) >= 104:
            raise ValueError("Kira runtime path is too long for a Unix socket; use a shorter "
                             "KIRA_HOME or, without KIRA_HOME, a shorter XDG_RUNTIME_DIR")
