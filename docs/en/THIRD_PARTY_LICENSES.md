# Third-party licenses

*[Leggi in italiano](../it/LICENZE_TERZE_PARTI.md)*

Kira Desktop is distributed under the MIT license (see [LICENSE](../../LICENSE)).
Dependencies keep their own licenses and their own copyright notices. This page
lists what Kira uses, under which license and where to find the full texts; the
exact versions and hashes of every distribution are in `uv.lock` and in
`packaging/requirements.lock.txt`.

## Qt and PySide6

Qt for Python (PySide6 Essentials) and Shiboken 6.11.2 are provided by The Qt
Company and their contributors under the LGPLv3, GPLv2, GPLv3 or commercial
license. Kira uses the **LGPLv3** option for the linked runtime. Qt and PySide
remain separate shared libraries, loaded dynamically: you can replace them with
compatible versions, modify them and debug those modifications; Kira does not
forbid reverse engineering for this purpose. The installed Qt bundle comes from
the official `PySide6-Essentials` wheel, whose SHA256 is in the lock; Kira
applies no patches to Qt.

The original source of the exact versions is available here:

- PySide/Shiboken: <https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.11.2>
- PySide source releases: <https://download.qt.io/official_releases/QtForPython/pyside6/>
- Qt 6.11.2 source modules with build files and licenses: <https://download.qt.io/archive/qt/6.11/6.11.2/submodules/>
- Qt source repository: <https://code.qt.io/cgit/qt/qt5.git/tag/?h=v6.11.2>

Anyone redistributing the Qt binaries must in turn provide access to the
corresponding source under the terms of those licenses, including any later
modifications. The texts of LGPLv3, GPLv3 and the Qt GPL exception are in
`packaging/licenses/` in the repository, together with the license of the
WebAssembly module described below.

## Python dependencies

| Dependency | License | Use in Kira |
| --- | --- | --- |
| markdown-it-py 4.0.0, mdurl | MIT | Markdown renderer for the assistant's replies; they keep the notices of the Python port and of the original markdown-it |
| HTTPX, httpcore, h11, AnyIO, IDNA | Permissive licenses of the respective projects (BSD/MIT) | HTTP transport towards Ollama, OpenAI Responses and DeepSeek |
| certifi | MPL 2.0 | Mozilla root certificate store |
| Pydantic, pydantic-core, annotated-types, typing-extensions, typing-inspection | MIT / PSF | Validation of contracts and messages |
| PyJWT | MIT | Token handling for the direct providers |
| cryptography | Apache-2.0 or BSD-3-Clause | Signature validation; includes the notices of the cryptographic libraries bundled in the wheel |
| CFFI | MIT-0 | Dependency of cryptography |
| pycparser | BSD-3-Clause | Dependency of CFFI |
| Playwright 1.64.0 | Apache-2.0 | Drives Kira's isolated browser; the wheel also contains the upstream driver and its notices |
| pyee | MIT | Dependency of Playwright |
| greenlet | MIT and PSF-2.0 | Dependency of Playwright |
| wasmtime | Apache-2.0 WITH LLVM-exception | Runs the DeepSeek Web proof-of-work module in a sandbox |

All full notices remain in the metadata of the installed wheels.

## Optional extra: Claude Code

`claude-agent-sdk` (MIT) is an optional extra (`uv sync --extra claude-code`)
that drives Claude Code for the **Claude Code · personal use** provider. It is
not part of the `.deb` package. Its wheel, downloaded from PyPI, also contains
the Claude Code executable, which Anthropic distributes under its own terms:
Kira uses that executable, or `claude` on the `PATH` if the wheel does not
contain it, and does not redistribute it. The extra also installs the SDK's
dependencies (including `mcp` and `jsonschema`), each under its own license.

## WebAssembly module for DeepSeek Web

`src/kira/backends/deepseek_pow_solver.wasm` is the WebAssembly module of the
**deepseek_pow_solver** project by AmanCode22, which solves the proof-of-work
challenge of the DeepSeek web endpoint. Kira includes, unmodified, the file of
release `v1.0.0`, compiled from Rust; source and build recipe are in the
project's repository: <https://github.com/AmanCode22/deepseek_pow_solver>.

The module is distributed under the MIT license, with additional terms from its
author: it is not affiliated with, endorsed by or sponsored by DeepSeek, and it
must not be used to violate a platform's terms of service, to resell or share
access, or on accounts you do not own. The full text is in
`packaging/licenses/deepseek_pow_solver-MIT.txt`.

The module imports nothing from the host and runs as pure computation inside the
wasmtime sandbox; its SHA-256 is pinned in `scripts/check_public_tree.py`, which
rejects any unreviewed modification.

## Components not included in the package

- **Chromium / Chrome for Testing and FFmpeg** are downloaded by the user with
  `kira-desktop browser-install` into the local profile. The upstream license
  files remain in the engine distribution inside the profile
  (`~/.local/share/kira/browser-engine/`); they are not part of Kira's license.
- **System and Python libraries** on Ubuntu come from the distribution packages
  (including `xvfb`, `libxtst6` and Chromium's X11 libraries) and keep the
  notices installed by those packages.
- Kira's package includes no model weights, Codex binaries or credentials, or
  user content.

## Where to find the notices in the installed package

| Path | Content |
| --- | --- |
| `/usr/share/doc/kira-desktop/copyright` | Kira's MIT license |
| `/usr/share/doc/kira-desktop/licenses/` | Texts of LGPLv3, GPLv3, the Qt GPL exception and the deepseek_pow_solver license |
| `/usr/share/doc/kira-desktop/dependencies.json` | Name, version, license and home page of every bundled dependency |
| `/usr/share/doc/kira-desktop/requirements.lock.txt` | Exact versions and hashes of the installed wheels |
| `/opt/kira-desktop/lib/*.dist-info/` | Metadata of every wheel with the license files provided by the project |

From source, the same metadata is in `.venv/lib/python*/site-packages/*.dist-info/`.
