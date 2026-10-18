"""Stdlib-only child: no project code runs before the service start handshake."""

import base64
import hashlib
import io
import json
import sys

MAX_HANDSHAKE = 512 * 1024


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
    sys.stdin = io.StringIO(arguments + "\n")
    sys.argv = ["project-tool:" + value["identity"]]
    namespace = {"__name__": "__main__", "__file__": sys.argv[0], "__package__": None}
    exec(code, namespace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
