"""Stdlib-only child: no project code runs before the service start handshake."""

import base64
import hashlib
import io
import json
import os
import sys

MAX_HANDSHAKE = 512 * 1024
# Files a tool creates get the conventional user modes, as for run_command,
# whatever umask the service runs with (the packaged unit uses UMask=0077).
TOOL_UMASK = 0o022


def provide_arguments(data: bytes) -> None:
    """Expose the arguments as standard input: sys.stdin, sys.stdin.buffer and fd 0.

    The service closes the handshake pipe, so descriptor 0 is replaced by an
    in-memory file holding only the arguments. Where that is unavailable the
    Python-level streams still carry them.
    """
    try:
        memory = os.memfd_create("kira-project-tool-arguments", os.MFD_CLOEXEC)
    except (AttributeError, OSError):
        sys.stdin = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8")
        return
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(memory, view):]
        os.lseek(memory, 0, os.SEEK_SET)
        os.dup2(memory, 0)
    finally:
        os.close(memory)
    sys.stdin = open(0, encoding="utf-8", closefd=False)


def main() -> int:
    line = sys.stdin.buffer.readline(MAX_HANDSHAKE + 1)
    if not line.endswith(b"\n") or len(line) > MAX_HANDSHAKE:
        return 65
    try:
        value = json.loads(line)
        if (not isinstance(value, dict) or set(value) != {"version", "source", "sha256", "arguments", "identity"}
                or value["version"] != 1 or not isinstance(value["arguments"], dict)
                or not isinstance(value["identity"], str) or len(value["identity"]) > 160):
            return 65
        source = base64.b64decode(value["source"], validate=True)
        if (not 1 <= len(source) <= 256 * 1024
                or hashlib.sha256(source).hexdigest() != value["sha256"]):
            return 65
        script = source.decode("utf-8", "strict")
        arguments = json.dumps(value["arguments"], allow_nan=False, ensure_ascii=False, separators=(",", ":"))
        if len(arguments.encode("utf-8")) > 32768:
            return 65
        code = compile(script, "project-tool:" + value["identity"], "exec")
    except (ValueError, TypeError, KeyError, UnicodeError, SyntaxError):
        return 65
    provide_arguments((arguments + "\n").encode("utf-8"))
    os.umask(TOOL_UMASK)
    sys.argv = ["project-tool:" + value["identity"]]
    namespace = {"__name__": "__main__", "__file__": sys.argv[0], "__package__": None}
    exec(code, namespace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
