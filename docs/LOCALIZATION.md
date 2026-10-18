# Interface languages (Desktop 0.3.10)

Settings offers **Italiano** and **English**. Italian is the source/default
language, including for existing profiles without a language preference. The
choice is a validated `language` string in private `config/ui.json`, saved using
the same atomic operation as other GUI preferences. It applies immediately and
survives reopening the application.

## Presentation boundary

Language selection changes application labels, dialogs, tooltips, accessible
names, status descriptions, metrics formatting and application-authored errors.
It never changes project/chat/member names, drafts, instructions, paths, model
identifiers, permissions, provider settings or conversation content. Tool output,
provider replies and original JSON remain literal. Selecting English does not
instruct a model to answer in English.

New team templates and newly added members use the selected language for their
initial editable names and roles. Changing language preserves existing drafts
and members, including those whose default text has not been edited.

QML uses `qsTr`; Python UI text uses the local translation helpers. Transient
application text can retain its template and literal parameters for retranslation.
Ordinary strings are never classified as translatable merely because their text
matches a catalog entry. Multi-argument QML formatting replaces placeholders once,
so a user value containing `%2` cannot become a second format operation.

Qt translators belong to the GUI engine and are removed when it is destroyed.
Language changes refresh derived view models without IPC requests or execution.
Numeric formatters use a dedicated `QLocale`; neither the desktop's locale nor
global provider configuration is changed. Native external applications retain
their own language settings.

## Catalog layout

All language data is bundled beneath `src/kira/ui/translations/`:

- `languages.json`: versioned registry of language codes and native display names.
- `it/qt.json`: Italian labels for Qt's standard buttons; other Italian UI text
  comes from the source.
- `en/core.json`, `en/qml.json`, `en/python.json`: English translations grouped by
  ownership. Each file is a plain JSON object mapping source text to translation.

Catalogs are local package data, not downloaded content or executable templates.
The loader validates language codes, shapes, entry/file/aggregate size limits and
conflicting duplicates. Unsupported preferences fall back to Italian. Missing
entries fall back to their original source text; invalid bundled data is reported.
The completeness check prevents missing English entries from passing release
validation.

The GUI compiles validated messages with the `lrelease` tool bundled in the pinned
PySide6 distribution and loads native Qt catalogs. Generated QM files live in a
private, content-addressed cache; their key includes source messages, QML contexts
and Qt version. Cache reuse verifies integrity, and publication is atomic. No
generated binary catalog is committed to the repository. Native translators keep
lazy QML loading independent of Python translation callbacks. A failed language
preparation preserves the currently applied preference.

## Adding a language

1. Add its code and native name to `languages.json`, retaining Italian.
2. Create a matching locale directory, using the complete English catalog keys
   as the message inventory, and translate the messages in JSON catalogs.
   Keep Python `{name}` and Qt `%1` placeholders and file-filter
   extensions intact. Translate Qt standard button labels for that language too.
3. Run `python3 scripts/check_translations.py` to check marked-message coverage,
   duplicate conflicts and placeholders. Application labels generated from static
   lookup tables also need catalog entries and UI review.
4. Run the relevant localization and GUI tests. Verify layouts at supported sizes,
   font fallback, text direction where applicable, persistence, literal user data
   and every dialog in the new language.

Adding a registered catalog makes the language available in the selector without
editing its business logic. Each new language still needs translation and layout
review. Backend/API text and user documents are separate from GUI localization.
