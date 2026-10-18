# Image attachments

Kira can prepare explicitly selected or pasted images for OpenAI direct. A local
path typed in a message is ordinary text: it does not authorize an image upload.
The composer shows the imported image before the user sends the message.

## Import and storage

`attachment.import {path}` accepts an explicit absolute path to a regular local
image. It returns `{attachment:{id,name,mime,width,height,bytes,sha256,preview_url}}`.
The service records the metadata; `preview_url` points to a private local PNG.
The original source path is not part of attachment metadata. Import itself does
not contact a model or upload anything.

Supported inputs are PNG, JPEG, WebP and non-animated GIF. Animated GIF, WebP and
APNG are rejected rather than reduced to their first frame. Input files are
limited to 32 MiB and 32 million pixels. Each imported PNG is limited to 8 MiB;
one message can contain at most four images totaling 16 MiB. Inputs exceeding a
limit fail explicitly: Kira does not resize, truncate, or silently remove them.

Normalization honors image orientation, converts tagged colors to sRGB, and
copies the decoded pixels into a new PNG without source text, EXIF, comments or
color-profile metadata. Image dimensions are preserved, except for exchanging
width and height when the recorded orientation requires a rotation.

Files reside below the selected profile's data directory in
`image-attachments/<attachment-id>.png`. The directory is private to the current
user; imported files are read-only. Source and cache reads refuse symlinks and
non-regular files. Each send verifies the cached file's ownership, permissions,
byte count and SHA-256 fingerprint. Modifying the originally selected source
after import does not change the preview or the eventual upload. A missing or
modified saved preview fails explicitly.

Image files are profile data, separate from application source. Include the
profile data directory in backups; a database-only backup does not contain the
image bytes. Removing an image from an unsent message does not upload it.

## Provider transport

The direct adapter sends `input_image` parts using an in-memory PNG data URL and
`detail: auto`. Each image is attached to the corresponding user message using
its run identifier; assistant replies and unrelated messages remain text.
Run and queue records hold attachment identifiers and metadata, never base64
payloads. The complete HTTP request, including image encoding, history and tool
results, must fit the adapter's 32 MiB input limit.

The direct service pages the complete stored archive into its
[durable context](DIRECT_CONTEXT.md). Checkpoints retain immutable asset references
and restore their verified bytes for a request. Current images remain attached
across the direct tool loop and take priority within the 16 MiB image budget.
Historical assets are restored in context order when they fit; repeated assets
need not be duplicated. Missing, modified, already included or over-budget
historical images have explicit notices. Original attachment links and files
remain available after compaction. Current selected images are never silently
dropped; a missing or invalid current attachment fails explicitly.

The direct capability indicates implemented image transport. It does not certify
that every catalog model or account accepts images; model rejection remains a
visible provider failure without an automatic model switch. The selected model,
reasoning effort, account and existing authentication flow remain unchanged.

These structured image attachments are currently supported by OpenAI direct.
Other backends must explicitly reject unsupported image sends instead of
discarding the attachment. Existing explicit file-reference workflows remain
separate from visual image input.

The durable prompt queue accepts the same ordered attachment identifiers. Its
text can be edited without changing the saved images; an image-only item is
valid. Queue insertion and attachment links commit together. Identical request
retries retain the original item, while changed attachment order or content is a
conflict. A saved image is verified again before dispatch; a missing or modified
image fails the item and pauses the queue. Recovery retains images and requires
the normal explicit queue resume.

## Qualification

Synthetic tests cover immutable previews, metadata removal, EXIF orientation,
animation rejection, limits, source changes, cache substitution, input-to-turn
matching, text-only paths, tool-loop retention, provider rejection and request
size enforcement. Such tests verify Kira's transport behavior; they are not
evidence of actual model image understanding. See [STATUS](STATUS.md) for current
qualification and [OpenAI direct](OPENAI_DIRECT.md) for authentication and
provider execution behavior.

The wire format follows the official [OpenAI image-input
guide](https://developers.openai.com/api/docs/guides/images-vision?api-mode=responses).

For an explicitly authorized real check,
`scripts/live_provider_continuity_check.py` performs three turns in a disposable
profile and project: Codex establishes a random marker, direct recalls it and
reads a generated image, then Codex recalls the direct response in the same Kira
chat. The image's random digits and shape counts are checked against independent
harness ground truth. The test rejects tool use, changed model/effort, reused
stale threads, extra chats and unconfirmed cleanup.

The existing ChatGPT account store must have no other owning Kira service.
Finish work, preserve drafts and stop the idle application/service before this
isolated check; do not copy or bypass the account-store lock.

Supply `--auth-directory` for the existing private ChatGPT authorization,
`--codex-model`, `--codex-effort`, `--direct-model`, `--direct-effort`, and a new
`--output` report file under ignored `build/` or private storage outside the
checkout. No model or effort is inferred or substituted. The script uses existing
authentication without copying credentials or modifying global configuration;
normal token refresh may occur and the three real turns consume allowance.
It saves a private synthetic image alongside its report. Reports contain durable
events and run/provider bindings, never image data URLs. Run `--help` to inspect
the interface without starting providers.
