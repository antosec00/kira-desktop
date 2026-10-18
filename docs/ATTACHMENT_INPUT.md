# Files and images in message inputs

## Explicit image attachments

The ordinary chat composer supports real image attachments with the OpenAI
direct provider. File-picker, local-file MIME paste/drop and image clipboard
paste prepare visible thumbnail/name/dimension cards with a remove control.
Text containing a path remains literal text; typing a path never reads or uploads
its contents in the direct composer. Codex/Ollama retain path-based attachment
input. Switching away from direct preserves pending image cards and blocks
sending those cards until the user returns to direct or removes them.

Preparation calls the local `attachment.import {path}` service method. The
service returns `{attachment:{id,name,mime,width,height,bytes,sha256,preview_url}}`
only after importing a normalized private PNG. The source is not modified.
There is no network upload during import. Sending and queueing use opaque IDs in
`run.start.attachments` / `queue.add.attachments`; paths and preview URLs are not
used as provider image references. The selected model remains explicit; an image
request rejected by the model is a visible failure, not a reason to switch models.
An image-only message is allowed.

The GUI persists image metadata and text together per project/chat in its
private atomic composer store. Successful send/queue acknowledgement removes
only the images captured by that request; images added later are retained.
An uncertain or rejected request retains its draft. Import completion checks
the input context, literal text and selection before adding anything. Switching
chat while preparation is pending cannot insert into the next chat.

The service stores normalized images in the private application data directory
`image-attachments/<uuid>.png`. Preview rendering derives this location from a
canonical UUID, requires an owned private regular file and rejects symlinks.
It never follows a network URL or arbitrary stored preview path. Sent-image
cards use recorded user-message attachment metadata. Copy/source text remains
literal; the service's events and export carry attachment metadata separately.

Image limits are four per message, 32 MiB per source file, 32 million pixels,
8 MiB per normalized PNG and 16 MiB total normalized data per message. Supported
source formats are PNG, JPEG, WebP and static GIF; animated files fail visibly.
Clipboard bitmap conversion uses the installed Qt codecs. Historical image
reuse follows the explicit bounded portable-history contract in
[OpenAI direct](OPENAI_DIRECT.md); retained local history does not imply that
all historical pixels were supplied in a particular request.

## File references and question answers

Non-image files in the direct composer, all Codex/Ollama composer attachments
and attachments in free-text question answers retain editable local-path
references. Selected existing files stay in place.
Clipboard-only question images are stored as private PNGs under the data
`attachments` directory and inserted as path text. Reading such a reference
requires provider tools and the existing permissions; it does not grant extra
filesystem access or guarantee vision.

A reference import accepts at most 64 files and 16,384 rendered path characters.
Image clipboard conversion for reference-only inputs is limited to 32 million
pixels and 32 MiB. Prompt and answer limits remain independent of these import
bounds. Original files and clipboard content are not modified. Preparation runs
off the GUI thread, shows progress/errors and disables sending for that input.

Removing a draft image or reference does not erase stored files or sent history.
There is no automatic expiry. Back up the application data directories as well
as SQLite; database backups do not contain image bytes.

## Windows and WSL input

Explicit local file URLs and host file-list clipboard entries are normalized to
accessible local paths, including mounted Windows drives and the current WSL
distribution. Missing files, inaccessible mounts and another distribution fail
visibly. Remote resources are never downloaded by paste or drop.

On an explicit paste under WSL, the GUI can query the Windows file list using a
fixed bounded PowerShell STA helper. There is no background clipboard monitor.
Local URLs and image MIME have priority; text remains text when there is no
Windows file list. Windows-to-Linux drag/drop depends on the desktop transport;
the file chooser and paste provide alternatives.

Synthetic Qt tests exercise MIME, local preparation, preview rendering, draft
recovery, acknowledgement races and the no-upload-before-send boundary. They
are separate from host clipboard transport and live model understanding; see
[STATUS](STATUS.md) for the evidence actually collected.
