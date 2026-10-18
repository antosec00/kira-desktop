# Markdown output — Desktop 0.3.2

## User behavior

Assistant messages render Markdown: headings, bold/italic/strikethrough, lists,
quotes, inline code, fenced/indented code, tables, rules and links. The existing
history renders on load; no provider turn or database rewrite is required.
Selection spans paragraphs and code in a single read-only TextArea. Ctrl+C copies
selected rendered text, including code without fence delimiters. The existing
Copia button preserves the original Markdown; Testo/Formato switches the view.
The source-view preference survives streaming edits but resets on delegate reuse.
Tool output, user messages, approvals, labels and team evidence remain literal.

## Presentation boundary

The service, providers, IPC and database schema remain unchanged. Formatting is
UI-derived data; original messages remain the authority for copy and export.
Version 0.3.2 requires a matching service through the existing hello handshake.
The deployment must preserve active work and the existing selected store.

A new pinned runtime dependency, markdown-it-py 4.0.0 (mdurl 0.1.2), parses
CommonMark plus tables and strikethrough with HTML parsing disabled. A small
allowlisted token renderer generates the subset of HTML understood by Qt's
RichText engine. This avoids exposing raw model HTML to Qt and avoids rewriting
the Markdown grammar by hand. No JavaScript engine or WebEngine is introduced.
The renderer is parented to the QML engine and does not outlive it.

Raw HTML is escaped and remains visible as literal text. Markdown images become
inert description text. Generated output contains no images, external styles,
resource imports or caller-provided attributes. Rendering performs no network
requests or file reads. Since 0.3.9, validated local appearance preferences supply
colors and fonts. Fenced code retains indentation, tabs and Unicode. Diff fences
can color added/removed lines while preserving their signs; general syntax
coloring and interactive task-list controls are not included. Optional smart
punctuation affects prose presentation only; see [Appearance](APPEARANCE.md).

The renderer bounds input to 131,072 characters, nesting to 20, output to
1,572,864 characters and cache memory to 2 MiB/64 entries. It falls back to
escaped source if generated output exceeds its limit. Persistent messages and
export retain their own existing service bounds.

## Explicit links

Links require a user click. HTTP/HTTPS URLs require a host and reject embedded
credentials. Local relative/absolute/file links are resolved against the current
project and must remain inside its canonical root. Only existing regular
non-executable documents are opened; script/program/launcher suffixes, executable
permission bits and recognized executable headers are blocked. The implementation
checks project identity and generation again after background validation.
Network-relative URLs, remote file hosts and other schemes are rejected.
This action opens a document through the desktop; it does not execute displayed
commands or authorize backend operations. File-line anchors are not editor
navigation instructions. Copying the original source remains available.

## Verification and scope

Tests use synthetic Markdown and disposable project files, actual Qt documents,
mouse/keyboard selection, clipboard contents, streamed updates and recycled
ListView delegates. Screenshots are real Qt renders with explicitly synthetic
content. Installed package smoke checks also render/copy a synthetic response
and confirm inert images using the bundled parser and QML from outside checkout.
No live model run is needed to verify a presentation-only transformation; previous
provider qualifications retain their original hashes and scope.

Current qualification is recorded in [STATUS.md](STATUS.md), separately from
historical private reports and their original package hashes. Use a separate
empty profile for preview and follow [operations](OPERATIONS.md) when upgrading
an existing store.

Primary references: [markdown-it-py security configuration](https://markdown-it-py.readthedocs.io/en/latest/security.html)
and [Qt TextEdit rich-text and clipboard API](https://doc.qt.io/qt-6/qml-qtquick-textedit.html).
