# Ubuntu 26.04 application package

This separate target builds the same Kira Desktop source and IPC contract with
Ubuntu 26.04's CPython 3.14. Noble remains the default build target and keeps
its Python 3.12 ABI dependency. The shared project metadata and `uv.lock` cover
both interpreters without changing the pinned runtime library versions.

```sh
python3 scripts/build_deb.py --target resolute
python3 scripts/check_deb.py --target resolute
```

Docker is required. The target builder uses the Ubuntu and uv images pinned by
digest in `source.json`. The checkout is mounted read-only; only
`build/targets/resolute/` and `dist/resolute/` are writable host mounts. No host
Python, desktop package, driver, account, or global configuration is installed or
changed. The container uses the invoking user's UID for its output files.

The builder exports the shared runtime lock with `uv export --frozen`, installs
binary wheels with hash verification, and builds the application wheel with the
separate hashed Hatchling build-tool lock. Package metadata explicitly requires
Python >=3.14,<3.15 and glibc >=2.43. This package must not be installed on Noble.

Outputs:

- `dist/resolute/kira-desktop_<version>_amd64.deb`
- `dist/resolute/SHA256SUMS`
- `dist/resolute/kira-desktop-<version>-build.json`

The build record includes the exact container image, interpreter and Qt versions,
source/runtime/build lock hashes, selected Python distributions, installed builder
packages, and artifact hash. Ubuntu package indices remain mutable; recorded
provenance is not a promise of byte-identical future builds.

The package validator installs into an owned disposable Ubuntu 26.04 container,
then runs the installed QML and actual user service outside the checkout as an
unprivileged user. It exercises persistence, backup/export, clipboard, offscreen
and Xvfb/XCB rendering, and package upgrade/removal/reinstallation with preserved
data. The default evidence directory is `build/releases/<version>/resolute/package/`.
This establishes container installation and application behavior. It does not
establish a native Plasma/Wayland desktop, hardware support, or a live provider
connection; those require separate system acceptance.
