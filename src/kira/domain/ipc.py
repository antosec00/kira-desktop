"""Where the local IPC lives: a private Unix socket, or authenticated loopback TCP.

Windows offers this stack no Unix socket, so the service listens on 127.0.0.1
and records its endpoint in the profile's runtime file. A loopback port has no
owner: any program on the computer can open it. The service therefore creates
a secret at every start, stores it next to the endpoint, and reads no command
from a connection until that connection has presented the secret.
"""

import hmac
import os
import secrets
import sys
from pathlib import Path

LOOPBACK = "127.0.0.1"
AUTH_METHOD = "auth"
#: An unauthenticated peer may hold a connection open only this long.
AUTH_TIMEOUT = 10.0
#: The authentication frame is small; nothing larger is parsed from a stranger.
AUTH_FRAME_BYTES = 4096
TOKEN_CHARACTERS = 64


def tcp_transport() -> bool:
    """Whether this platform uses loopback TCP instead of a Unix socket."""
    return sys.platform == "win32"


def new_token() -> str:
    return secrets.token_hex(TOKEN_CHARACTERS // 2)


def token_matches(expected: str, supplied: object) -> bool:
    return (bool(expected) and isinstance(supplied, str)
            and hmac.compare_digest(expected.encode(), supplied.encode()))


def write_endpoint(path: Path, port: int, token: str) -> None:
    """Publish the listening port and its secret together, never one without the other."""
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(f"{LOOPBACK}:{port}\n{token}\n")
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def read_endpoint(path: Path) -> tuple[str, int, str]:
    """Return host, port and secret of the running service.

    The secret is empty when the file was written by a service that predates
    authentication: such a service is still reachable, so that this version
    can ask it to stop. Raises OSError or ValueError for a missing or invalid
    file.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or len(lines) > 2:
        raise ValueError("Invalid Kira service endpoint")
    host, separator, port_text = lines[0].strip().rpartition(":")
    token = lines[1].strip() if len(lines) == 2 else ""
    if not separator or host != LOOPBACK or not port_text.isdecimal() or not 0 < int(port_text) < 65536:
        raise ValueError("Invalid Kira service endpoint")
    if token and (len(token) != TOKEN_CHARACTERS or any(c not in "0123456789abcdef" for c in token)):
        raise ValueError("Invalid Kira service secret")
    return host, int(port_text), token
