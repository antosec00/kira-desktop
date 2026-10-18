# Compact tool output (Desktop 0.3.4)

Desktop 0.8.5 additionally groups consecutive tools from the same run under an
expandable activity summary; see [Chat layout](CHAT_LAYOUT.md). Group expansion
reveals the original tool cards described below. It does not replace their
individual output controls, raw details, copy/export data or attention states.

Tool cards start collapsed. The model supplies the first 12 lines, capped at
1,600 characters, as an exact prefix. QML also caps the visible preview at about
144 logical pixels, aligned to complete font lines, so wrapped lines remain
compact. Short results need no toggle. Long results offer **Mostra tutto** and
**Riduci**, accessible through keyboard and pointer input. Command, exit status,
error indicators, Dati, Copia comando and Copia remain available.

Expansion restores the full body already available in the transcript. Copia
always copies that body, independently of preview state. Existing upstream and
transcript limits still apply (131,072 characters per displayed body, with an
export notice); expansion cannot recover bytes absent from the recorded output.
Stored events, artifact access and history export are unchanged. Assistant
Markdown rendering and user messages are unaffected; tool output stays literal.

Expansion belongs to the GUI model row and survives streaming updates and
recycled delegates. Reloading history resets cards to compact mode. No database
migration, provider call, model selection or service change is involved.

Toggling retains the card's viewport anchor and disables automatic following,
including previously queued callbacks. **Ultimi aggiornamenti** returns to the
latest output and resumes following. Visual tests use synthetic generic output;
they do not execute model tasks or claim live provider qualification.
