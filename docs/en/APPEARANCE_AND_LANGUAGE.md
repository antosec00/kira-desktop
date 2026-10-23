# Interface appearance and language

*[Leggi in italiano](../it/ASPETTO_E_LINGUA.md)*

This guide describes the options in **Settings → Appearance**: light or dark
theme, colors, fonts, animations, conversation width, theme exchange and
interface language. They are visual preferences of your profile: they are
saved immediately, apply even while Kira is working and do not touch
providers, models, permissions or projects.

## Light and dark mode

In **Settings → Appearance → Mode** choose **Light**, **Dark** or **Follow
system**, which follows the desktop theme as reported by Qt. The same switch
between light and dark is available with the toggle at the top right of the
main bar. The initial theme is the dark one.

## Themes and colors

The **Theme** menu offers three presets, each with a light and a dark palette:

| Preset | Dark | Light |
| --- | --- | --- |
| **Kira** | warm graphite with an amber accent | ivory background, darker amber accent |
| **Neutral** | neutral gray with a violet accent | neutral white, violet accent |
| **Warm** | dark brown with a light amber accent | cream background, brown-orange accent |

Below the preset you can override three colors: **Accent color**,
**Background** and **Text**. Enter a six-digit hexadecimal code (for example
`#d9a05b`) and confirm with **Enter**; **Auto** clears the override and goes
back to the preset color. The **Preview** shows the result immediately with
text, code and a `diff` block, so you can check the readability of the
background/text pair you chose: Kira does not correct it for you.

## Fonts

In the **Fonts** section choose family, style and size for three distinct
roles:

| Role | Default choice | Size |
| --- | --- | --- |
| **Interface** | **System font** | 12-18 px |
| **Conversation** | **Same as interface** | 12-20 px |
| **Code** | **System monospace** | 10-20 px |

The available families are those installed on the computer: Kira does not
download fonts. For each role you can choose the regular, medium, semibold,
bold or italic style. The code font applies to code blocks and spans in the
replies and to the literal views of output and source.

## Customize light and dark separately

Normally preset, colors and fonts are shared between the two modes. By turning
on **Customize light and dark separately**, changes apply only to the mode
visible at that moment: the panel says "You are editing the dark theme" or
"the light theme", and the **Light** and **Dark** buttons take you to the
other one to customize it. On activation the shared style is copied into
both; when you turn the option off, the style visible at that moment becomes
the shared one.

## Interface details

- **Reduce motion**: **Follow system** reads the preference exposed by the
  desktop; if the system does not expose one, a note appears and animations
  stay on: choose **Always** to reduce them, **Never** to keep them on. With
  reduced motion the dots of the working states stop too.
- **Translucent sidebar** lets Kira's background show through the sidebar. It
  does not enable compositor blur or transparency toward other windows.
- **Element contrast** (from 0 to 100) adjusts secondary text and borders. It
  does not change the main colors you chose.
- **Full-width conversation**: by default the messages occupy a centered,
  limited reading column; turn the option on to use all the available space.
- **Pointer cursor on controls** shows the hand cursor on interactive
  controls.

## Smart punctuation

**Smart punctuation** is off at the start. When it is on, it converts quotes,
dashes and the like only in the **display** of the prose of formatted
replies. It does not touch what you write in the composer, the saved
messages, the request sent to the provider, code blocks and spans, or the
exports.

Selecting the displayed text and pressing **Ctrl+C** copies the punctuation
you see; the message's **Copy** button, the **Text** view and the export keep
the original Markdown.

## Change markers in code

For `diff` and `patch` blocks in the replies choose **Color and markers** or
**Markers only**. In both cases the original `+` and `-` characters stay in
place; the only difference is whether added and removed lines are also
colored. Tool output always remains literal text.

Changing the theme, font or markers also updates the messages already in the
chat immediately, without modifying the history and without a new model turn.

## Copying, exporting, importing and resetting a theme

At the bottom of the section you find four buttons:

- **Copy theme** copies to the clipboard a JSON with the appearance
  preferences only.
- **Export theme** shows the same JSON in a window, to save wherever you
  want.
- **Import theme** accepts a JSON in the `kira-desktop-appearance` format,
  version 1: paste it to apply it on this or another installation. The
  content is validated field by field; an invalid file or a failed save leaves
  the previous settings intact and shows an error. A theme cannot contain
  paths, credentials, provider configuration or executable code.
- **Reset appearance** brings all the visual preferences back to their
  initial values. It touches nothing else.

Fonts are **not** transferred with the theme: the JSON carries only their
name. If a family is missing on another machine, Qt uses an alternative.

All appearance changes are saved immediately in `ui.json` in the profile (see
[Installation](INSTALLATION.md)), even while a request is in progress. The
Codex configuration, the desktop theme and the other applications stay
intact.

## Interface language

In **Settings → Appearance → Interface language** choose **Italiano** or
**English**. The change is immediate, requires no restart and is remembered
when you reopen the app. Italian is the starting language and stays active
for profiles that have never chosen otherwise.

Commands, windows, descriptions, tooltips, accessible names, statuses, number
formats and error messages generated by the app are translated. The following
stay as they are:

- names of projects, chats and Team members, drafts and instructions;
- paths, model identifiers, provider settings and messages;
- the contents of the conversation, the models' replies, tool output and the
  original JSON.

Choosing **English** does not ask the model to reply in English: the language
of the replies depends on your requests and, if you use a Soul, on the
**Response language** preference (see [Soul and
Knowledge](SOUL_AND_KNOWLEDGE.md)).

New Teams and members added after the change receive their initial names and
roles in the chosen language; those already present keep their own text, even
if you had never edited it. External applications opened by Kira keep their
own language. To add a language to the catalogs see the
[developer guide](DEVELOPMENT.md).

## Density and other presentation preferences

Some presentation choices live in **Settings → Work**, because they concern
the way of working more than the colors:

- **Interface density**: **Comfortable** (default) or **Compact**, with
  tighter spacing;
- **Sidebar**: **Whole**, **Icons only** or **Hidden** (also with
  **Ctrl+B**);
- **Labels next to the tool icons** for the tray below the composer;
- **Open the model options with the chat** to make the **Provider, model and
  reasoning** window appear on its own.

These options are described in detail in the [User guide](USER_GUIDE.md).
Like the appearance ones, they are saved immediately and do not change
providers, permissions or chats.
