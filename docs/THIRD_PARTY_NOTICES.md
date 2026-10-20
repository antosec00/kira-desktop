# Third-party dependencies

Kira Desktop's application source is MIT licensed; see LICENSE. Dependencies retain
their own licenses and copyright notices. The exact distribution versions and
hashes are in `uv.lock` and `packaging/requirements.lock.txt`. The installed package
includes `dependencies.json`, wheel metadata, and each wheel's supplied license
files under `/opt/kira-desktop/lib/*.dist-info/`.

Qt for Python / PySide6 Essentials and Shiboken 6.11.2 are provided by The Qt
Company and contributors under their LGPLv3/GPLv3/commercial licensing terms.
Kira uses the LGPL option for the linked runtime. LGPLv3, GPLv3 and Qt's GPL
exception texts are installed under `/usr/share/doc/kira-desktop/licenses/`.
Qt and PySide remain separate, dynamically loaded shared libraries; users may
replace compatible libraries, modify them and debug such modifications. Kira
imposes no prohibition on reverse engineering for that purpose.

Unmodified upstream source for the precise versions can be obtained from:

- PySide/Shiboken: https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.11.2
- PySide source releases: https://download.qt.io/official_releases/QtForPython/pyside6/
- Qt 6.11.2 source modules and their build files/licenses:
  https://download.qt.io/archive/qt/6.11/6.11.2/submodules/
- Qt source repository: https://code.qt.io/cgit/qt/qt5.git/tag/?h=v6.11.2

The installed Qt bundle comes from the official `PySide6-Essentials` wheel,
whose exact SHA256 is in the lock; no Qt patches are applied by Kira. A distributor
publishing binaries must also provide corresponding source access under those
licenses, including any later modifications they make.

Desktop 0.3.2 also includes markdown-it-py 4.0.0 and mdurl 0.1.2 under their MIT
licenses. The original Python-port and upstream markdown-it notices are retained
in the wheels' `.dist-info/licenses/` and `.dist-info/LICENSE` paths.

Other runtime dependencies: HTTPX/httpcore/h11/AnyIO/IDNA, Pydantic/pydantic-core,
annotated-types, typing-extensions and typing-inspection use their upstream
permissive licenses; certifi's Mozilla trust-store distribution uses MPL 2.0.
Their full notices are preserved in wheel metadata and license directories.
Ubuntu system libraries and Python are supplied by Ubuntu packages, retaining
the notices installed by those packages. The Kira Desktop application package
bundles no model weights, Codex binaries, credentials or user content.

Desktop 0.6 adds PyJWT (MIT), cryptography (Apache-2.0 OR BSD-3-Clause),
CFFI (MIT) and pycparser (BSD-3-Clause) for signature validation. Their upstream
wheel notices, including bundled cryptographic-library notices, remain in the
installed dependency metadata. Exact versions and hashes are in the lock.

The web/browser additions use Playwright 1.64.0 (Apache-2.0), pyee (MIT) and
greenlet (MIT and PSF-2.0), with their supplied wheel notices preserved. The
Playwright wheel also contains its upstream driver and accompanying notices.
Chromium/Chrome for Testing and FFmpeg are downloaded separately by explicit
user setup; their upstream license/notice files remain in the profile-local
engine distribution. They are not part of Kira's application source license.

## Added in 0.16–0.18 development

- `playwright` (Apache-2.0) drives Kira's isolated browser; browser engines are
  installed separately by the user and keep their own licenses.
- `PyJWT` (MIT) and `wasmtime` (Apache-2.0 WITH LLVM-exception) support the
  experimental DeepSeek account and web providers.
- `claude-agent-sdk` (MIT, optional `claude-code` extra, not packaged) drives the
  locally signed-in Claude Code for the personal-use provider; Claude Code itself
  is installed and licensed by Anthropic to the signed-in user.
- `src/kira/backends/deepseek_pow_solver.wasm` is a small Rust-compiled
  WebAssembly helper that solves the proof-of-work challenge of the experimental
  DeepSeek Web endpoint. It imports nothing and runs as pure computation inside
  the wasmtime sandbox; its SHA-256 is pinned in `scripts/check_public_tree.py`.
  The Rust source and build recipe are not yet part of this repository; they
  must be added or the module rebuilt from documented source before a public
  release that includes the DeepSeek Web provider.
