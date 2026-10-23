import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

KiraPanel {
    id: dialog
    objectName: "kiraSoulsDialog"
    title: qsTr("Anima di Kira")
    fallbackWidth: 1040
    fallbackHeight: 820
    property bool archiveConfirmation: false
    property bool importVisible: false
    KFormat { id: kformat }
    component PreferenceEditor: ColumnLayout {
        id: preferenceEditor
        required property string preferenceKey
        readonly property var entry: souls.preferences.find(function(row) { return row.key === preferenceKey }) || {label: "", editedValue: ""}
        Layout.fillWidth: true
        Label { text: preferenceEditor.entry.label; font: appearance.uiFont; color: appearance.colors.muted }
        RowLayout {
            Layout.fillWidth: true
            TextField {
                id: preferenceField
                objectName: "soulPreference_" + preferenceEditor.preferenceKey
                Layout.fillWidth: true; Layout.minimumWidth: 0
                text: preferenceEditor.entry.editedValue; selectByMouse: true; font: appearance.uiFont
                enabled: !souls.busy && !souls.pending && !souls.selected.archived
                Accessible.name: preferenceEditor.entry.label
                onTextEdited: souls.editPreference(preferenceEditor.preferenceKey, text)
            }
            KButton { text: qsTr("Salva"); enabled: souls.canEdit && !souls.dirty && !souls.selected.archived && preferenceEditor.entry.editedValue !== preferenceEditor.entry.value; onClicked: souls.setPreference(preferenceEditor.preferenceKey, preferenceField.text) }
        }
    }
    onOpened: souls.open()
    onClosed: { souls.hide(); archiveConfirmation = false }
    footer: Flow {
        padding: 12; spacing: 6
        KButton { objectName: "soulBind"; text: souls.isNewChat ? qsTr("Usa nella nuova chat") : qsTr("Usa in questa chat"); primary: true; enabled: souls.canBind && souls.selected.id.length > 0 && !souls.selected.archived && !souls.dirty; onClicked: souls.applyToChat() }
        KButton { objectName: "soulDisable"; text: qsTr("Kira senza personalizzazione"); enabled: souls.canBind && souls.hasSelection; onClicked: souls.disableForChat() }
        KButton { text: qsTr("Chiudi"); onClicked: dialog.close() }
    }
    contentItem: ColumnLayout {
        spacing: 10
        ReadText {
            objectName: "soulIntro"; Layout.fillWidth: true
            text: souls.activeName.length ? (souls.isNewChat ? qsTr("Nella nuova chat: %1") : qsTr("In questa chat: %1")).arg(souls.activeName) : qsTr("La tua Kira, il tuo modo di collaborare.")
            font.bold: true; color: appearance.colors.accent
        }
        ReadText { visible: !dialog.compactPanel; Layout.fillWidth: true; text: qsTr("Identità e preferenze restano separate dal modello e dal lavoro della chat."); color: appearance.colors.muted }
        ReadText { objectName: "soulError"; Layout.fillWidth: true; visible: text.length > 0; text: souls.error; color: appearance.colors.warning }
        RowLayout {
            visible: souls.pending; Layout.fillWidth: true
            ReadText { text: qsTr("Una modifica attende conferma dal servizio."); color: appearance.colors.warning; Layout.fillWidth: true }
            KButton { text: qsTr("Verifica richiesta"); enabled: souls.available && !souls.busy; onClicked: souls.retryPending() }
        }
        ReadText { objectName: "soulNotice"; Layout.fillWidth: true; visible: text.length > 0; text: souls.notice; color: appearance.colors.success }
        GridLayout {
            id: columns
            columns: dialog.availableWidth >= 730 ? 2 : 1
            Layout.fillWidth: true; Layout.fillHeight: true
            columnSpacing: 18; rowSpacing: 10
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: columns.columns === 2
                Layout.preferredWidth: columns.columns === 2 ? 210 : -1
                Layout.maximumWidth: columns.columns === 2 ? 260 : 100000
                spacing: 8
                RowLayout {
                    Layout.fillWidth: true
                    ReadText { visible: columns.columns === 2; text: qsTr("Le mie Kira"); font.bold: true; Layout.fillWidth: true }
                    KComboBox { objectName: "soulPicker"; visible: columns.columns === 1; Layout.fillWidth: true; Layout.minimumWidth: 60; model: souls.items; textRole: "label"; valueRole: "id"; currentIndex: indexOfValue(souls.selected.id); displayText: currentIndex >= 0 ? currentText : qsTr("Le mie Kira"); enabled: !souls.busy; onActivated: souls.select(currentValue); Accessible.name: qsTr("Le mie Kira") }
                    KButton { objectName: "soulNew"; text: "+"; toolTipText: qsTr("Crea una Kira"); Accessible.name: toolTipText; enabled: !souls.busy; onClicked: { dialog.archiveConfirmation = false; souls.newIdentity() } }
                    KButton { text: "↻"; toolTipText: qsTr("Aggiorna"); Accessible.name: toolTipText; enabled: souls.available && !souls.busy; onClicked: souls.refresh() }
                }
                Rectangle {
                    visible: columns.columns === 2
                    Layout.fillWidth: true; Layout.fillHeight: columns.columns === 2
                    Layout.preferredHeight: columns.columns === 2 ? 320 : 108
                    color: appearance.colors.surfaceAlt; radius: 10; border.color: appearance.colors.border
                    ListView {
                        id: soulList; objectName: "soulList"; anchors.fill: parent; anchors.margins: 5; clip: true
                        model: souls.items; spacing: 3
                        ScrollBar.vertical: KScrollBar {}
                        delegate: ItemDelegate {
                            required property var modelData
                            width: soulList.width; implicitHeight: nameText.implicitHeight + 20
                            enabled: !souls.busy
                            background: Rectangle { color: souls.selected.id === modelData.id ? appearance.colors.hover : "transparent"; radius: 7 }
                            contentItem: Label { id: nameText; text: modelData.label; textFormat: Text.PlainText; wrapMode: Text.Wrap; font: appearance.uiFont; color: souls.selected.id === modelData.id ? appearance.colors.accent : appearance.colors.text }
                            onClicked: { dialog.archiveConfirmation = false; souls.select(modelData.id) }
                        }
                        Label { anchors.centerIn: parent; visible: soulList.count === 0; text: qsTr("Crea la tua prima Kira"); font: appearance.uiFont; color: appearance.colors.muted }
                    }
                }
                KButton { visible: souls.pages.souls === true; text: qsTr("Mostra altre"); enabled: !souls.busy; onClicked: souls.more("souls") }
                Flow {
                    Layout.fillWidth: true; spacing: 4
                    KButton { text: qsTr("Importa profilo"); enabled: souls.canEdit; onClicked: dialog.importVisible = !dialog.importVisible }
                    KButton { text: qsTr("Copia profilo"); visible: souls.selected.id.length > 0; enabled: souls.available && !souls.busy; onClicked: souls.exportProfile() }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 0
                Flow {
                    Layout.fillWidth: true; spacing: 5
                    KButton { objectName: "soulIdentityTab"; text: qsTr("Identità"); checked: souls.view === "identity"; enabled: !souls.busy; onClicked: souls.setView("identity") }
                    KButton { objectName: "soulMemoryTab"; text: qsTr("Cosa ha imparato"); checked: souls.view === "memories"; enabled: !souls.busy && souls.selected.id.length > 0; onClicked: souls.setView("memories") }
                    KButton { objectName: "soulHistoryTab"; text: qsTr("Evoluzione"); checked: souls.view === "history"; enabled: !souls.busy && souls.selected.id.length > 0; onClicked: souls.setView("history") }
                }
                ScrollView {
                    id: scroll; objectName: "soulEditorScroll"
                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 80
                    clip: true; contentWidth: availableWidth; rightPadding: 14
                    ScrollBar.vertical: KScrollBar { parent: scroll; x: scroll.width - width; y: 0; height: scroll.height; orientation: Qt.Vertical }
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ColumnLayout {
                        width: scroll.availableWidth; spacing: 12
                        ColumnLayout {
                            visible: dialog.importVisible; Layout.fillWidth: true
                            ReadText { text: qsTr("Incolla un profilo esportato. Verrà creata una nuova Kira, senza chat né ricordi."); Layout.fillWidth: true }
                            TextArea { objectName: "soulImportText"; Layout.fillWidth: true; Layout.preferredHeight: 130; wrapMode: TextEdit.Wrap; selectByMouse: true; textFormat: TextEdit.PlainText; font: appearance.codeFont; text: souls.importText; onTextChanged: if (text !== souls.importText) souls.setImportText(text); Accessible.name: qsTr("Profilo da importare") }
                            KButton { text: qsTr("Crea dal profilo"); enabled: souls.canEdit && souls.importText.length > 0; onClicked: souls.importProfile() }
                        }
                        ColumnLayout {
                            visible: souls.source.id.length > 0; Layout.fillWidth: true
                            ReadText { text: qsTr("Fonte originale"); font.bold: true; Layout.fillWidth: true }
                            ReadText { text: souls.source.kind || ""; Layout.fillWidth: true; color: appearance.colors.muted }
                            ReadText { text: souls.sourceLocation; visible: text.length > 0; Layout.fillWidth: true; color: appearance.colors.muted }
                            ReadText { objectName: "soulSourceContent"; text: souls.source.content; Layout.fillWidth: true }
                            Flow {
                                Layout.fillWidth: true; spacing: 5
                                KButton { text: qsTr("Continua a leggere"); visible: souls.source.next_offset !== null && souls.source.next_offset !== undefined; enabled: !souls.busy; onClicked: souls.nextSourcePage() }
                                KButton { text: qsTr("Chiudi fonte"); onClicked: souls.closeSource() }
                            }
                        }
                        ColumnLayout {
                            visible: souls.view === "identity"; Layout.fillWidth: true; spacing: 10
                            Label { text: qsTr("Nome"); font: appearance.uiFont; color: appearance.colors.muted }
                            TextField { objectName: "soulName"; Layout.fillWidth: true; font: appearance.uiFont; selectByMouse: true; enabled: !souls.busy && !souls.pending && !souls.selected.archived; text: souls.draft.name; placeholderText: qsTr("Per esempio: Kira Studio"); Accessible.name: qsTr("Nome dell’identità"); onTextEdited: souls.edit(text, souls.draft.core, souls.draft.mode) }
                            ReadText { text: qsTr("Filosofia e modo di collaborare"); Layout.fillWidth: true; font.bold: true }
                            TextArea {
                                objectName: "soulCore"; Layout.fillWidth: true; Layout.preferredHeight: dialog.compactPanel ? 130 : 160
                                padding: 10
                                background: Rectangle { color: appearance.colors.background; radius: 8; border.color: parent.activeFocus ? appearance.colors.accent : appearance.colors.border }
                                font: appearance.uiFont; wrapMode: TextEdit.Wrap; selectByMouse: true; textFormat: TextEdit.PlainText
                                enabled: !souls.busy && !souls.pending && !souls.selected.archived; text: souls.draft.core
                                placeholderText: qsTr("Descrivi ciò che conta nel vostro modo di lavorare insieme…")
                                Accessible.name: qsTr("Filosofia dell’identità")
                                onTextChanged: if (text !== souls.draft.core) souls.edit(souls.draft.name, text, souls.draft.mode)
                            }
                            ReadText { text: qsTr("Nucleo: %1 / 1.200 byte UTF-8").arg(souls.coreBytes); color: souls.coreBytes > 1200 ? appearance.colors.warning : appearance.colors.muted; Layout.fillWidth: true }
                            ReadText { text: qsTr("Il nucleo breve viene fornito a ogni turno. Esempi e lezioni restano nella memoria consultabile."); Layout.fillWidth: true; color: appearance.colors.muted }
                            RowLayout {
                                Layout.fillWidth: true
                                Label { text: qsTr("Come evolve"); font: appearance.uiFont; color: appearance.colors.text }
                                KComboBox { objectName: "soulMode"; Layout.fillWidth: true; Layout.minimumWidth: 90; model: souls.modes; textRole: "label"; valueRole: "value"; currentIndex: indexOfValue(souls.draft.mode); enabled: !souls.busy && !souls.pending && !souls.selected.archived; onActivated: souls.edit(souls.draft.name, souls.draft.core, currentValue); Accessible.name: qsTr("Modalità di apprendimento") }
                            }
                            ReadText {
                                Layout.fillWidth: true; color: appearance.colors.muted
                                text: souls.draft.mode === "manual" ? qsTr("Cambi soltanto tu l’identità e le preferenze.") : souls.draft.mode === "adaptive" ? qsTr("Il comando /anima preferenza applica le tue preferenze. Le lezioni proposte dal modello richiedono la tua scelta; ogni modifica resta tracciata.") : qsTr("Kira propone ciò che potrebbe imparare. Decidi tu cosa conservare.")
                            }
                            ReadText { visible: souls.draft.mode === "adaptive"; text: qsTr("Esempio in chat: /anima preferenza tono=naturale e diretto"); Layout.fillWidth: true; color: appearance.colors.muted }
                            ReadText { visible: souls.staleDraft; text: qsTr("Esiste una revisione più recente. La tua bozza è conservata: confrontala prima di salvarla."); Layout.fillWidth: true; color: appearance.colors.warning }
                            ReadText { visible: souls.staleDraft; text: qsTr("Versione salvata: %1").arg(souls.selected.core); Layout.fillWidth: true }
                            Flow {
                                Layout.fillWidth: true; spacing: 6
                                KButton { objectName: "soulSave"; text: souls.selected.id.length ? qsTr("Salva identità") : qsTr("Crea identità"); primary: true; enabled: souls.canSave; onClicked: souls.save() }
                                KButton { text: qsTr("Anteprima"); enabled: souls.available && !souls.busy && souls.draft.name.trim().length > 0; onClicked: souls.previewDraft() }
                                KButton { visible: souls.staleDraft; text: qsTr("Usa la bozza sulla nuova revisione"); enabled: souls.canEdit; onClicked: souls.rebaseDraft() }
                            }
                            Rectangle {
                                Layout.fillWidth: true; implicitHeight: previewColumn.implicitHeight + 22
                                color: appearance.colors.surfaceAlt; border.color: appearance.colors.border; radius: 10
                                ColumnLayout {
                                    id: previewColumn; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 11; spacing: 8
                                    ReadText { text: souls.dirty ? qsTr("Anteprima della bozza") : qsTr("Nucleo salvato"); font.bold: true; Layout.fillWidth: true }
                                    ReadText { objectName: "soulPreview"; text: souls.preview.length ? souls.preview : qsTr("Premi Anteprima per vedere il testo esatto del nucleo."); Layout.fillWidth: true }
                                    ReadText { visible: souls.dirty && souls.preview.length > 0; text: qsTr("La versione definitiva sarà disponibile dopo il salvataggio."); Layout.fillWidth: true; color: appearance.colors.muted }
                                    ReadText { text: souls.previewSize; visible: text.length > 0; color: appearance.colors.muted; Layout.fillWidth: true }
                                }
                            }
                            ColumnLayout {
                                visible: souls.selected.id.length > 0; Layout.fillWidth: true
                                ReadText { text: qsTr("Preferenze esplicite"); font.bold: true; Layout.fillWidth: true }
                                PreferenceEditor { preferenceKey: "tone" }
                                PreferenceEditor { preferenceKey: "detail" }
                                PreferenceEditor { preferenceKey: "collaboration" }
                                PreferenceEditor { preferenceKey: "language" }
                                ReadText { text: qsTr("Una preferenza vuota rimuove la personalizzazione di quel campo."); Layout.fillWidth: true; color: appearance.colors.muted }
                            }
                            Flow {
                                visible: souls.selected.id.length > 0; Layout.fillWidth: true; spacing: 6
                                KButton { text: qsTr("Duplica"); enabled: souls.canEdit && !souls.dirty; onClicked: souls.duplicate() }
                                KButton { visible: !souls.selected.archived; text: qsTr("Archivia"); enabled: souls.canEdit && !souls.dirty; onClicked: dialog.archiveConfirmation = !dialog.archiveConfirmation }
                            }
                            ReadText { visible: dialog.archiveConfirmation; text: qsTr("L’identità archiviata non sarà utilizzata nei nuovi turni. Potrai ripristinarla da Evoluzione."); color: appearance.colors.warning; Layout.fillWidth: true }
                            KButton { visible: dialog.archiveConfirmation; text: qsTr("Conferma archiviazione"); enabled: souls.canEdit && !souls.dirty; onClicked: { dialog.archiveConfirmation = false; souls.archive() } }
                        }
                        ColumnLayout {
                            visible: souls.view === "memories"; Layout.fillWidth: true; spacing: 12
                            ReadText { text: souls.memoryContext; color: appearance.colors.muted; Layout.fillWidth: true }
                            ReadText { text: qsTr("Proposte e decisioni"); Layout.fillWidth: true; font.bold: true }
                            ReadText { visible: souls.proposals.length === 0; text: qsTr("Nessuna proposta in questo contesto."); Layout.fillWidth: true; color: appearance.colors.muted }
                            Repeater {
                                model: souls.proposals
                                delegate: ColumnLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    ReadText { text: modelData.effect_label; Layout.fillWidth: true; font.bold: true }
                                    ReadText { text: modelData.status_label; Layout.fillWidth: true; color: modelData.status === "pending" ? appearance.colors.accent : appearance.colors.muted }
                                    ReadText { text: modelData.statement || modelData.value || ""; Layout.fillWidth: true }
                                    ReadText { text: modelData.applicability || ""; Layout.fillWidth: true; color: appearance.colors.muted }
                                    Flow {
                                        Layout.fillWidth: true; spacing: 5
                                        KButton { text: qsTr("Conserva"); visible: modelData.status === "pending"; enabled: souls.canEdit && !souls.selected.archived; onClicked: souls.decide(modelData.id, "accept") }
                                        KButton { text: qsTr("Scarta"); visible: modelData.status === "pending"; enabled: souls.canEdit && !souls.selected.archived; onClicked: souls.decide(modelData.id, "reject") }
                                        KButton { text: qsTr("Fonte"); visible: !!modelData.source_id; enabled: souls.canReadSource; onClicked: souls.readSource(modelData.source_id) }
                                    }
                                }
                            }
                            KButton { visible: souls.pages.proposals === true; text: qsTr("Altre proposte"); enabled: !souls.busy; onClicked: souls.more("proposals") }
                            ReadText { text: qsTr("Esperienza conservata"); Layout.fillWidth: true; font.bold: true }
                            ReadText { text: qsTr("Il feedback orienta quali ricordi richiamare."); Layout.fillWidth: true; color: appearance.colors.muted }
                            ReadText { visible: souls.memories.length === 0; text: qsTr("Nessun ricordo disponibile in questo contesto."); Layout.fillWidth: true; color: appearance.colors.muted }
                            Repeater {
                                model: souls.memories
                                delegate: ColumnLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    ReadText { text: modelData.statement || ""; Layout.fillWidth: true }
                                    ReadText { text: modelData.applicability || ""; Layout.fillWidth: true; color: appearance.colors.muted }
                                    Flow {
                                        Layout.fillWidth: true; spacing: 5
                                        KButton { text: qsTr("Fonte"); visible: !!modelData.source_id; enabled: souls.canReadSource; onClicked: souls.readSource(modelData.source_id) }
                                        KButton { text: qsTr("Utile"); enabled: souls.canEdit && !souls.selected.archived; onClicked: souls.feedback(modelData.id, true) }
                                        KButton { text: qsTr("Non utile"); enabled: souls.canEdit && !souls.selected.archived; onClicked: souls.feedback(modelData.id, false) }
                                        KButton { text: qsTr("Dimentica"); enabled: souls.canEdit; onClicked: souls.forget(modelData.id) }
                                    }
                                }
                            }
                            KButton { visible: souls.pages.memories === true; text: qsTr("Altri ricordi"); enabled: !souls.busy; onClicked: souls.more("memories") }
                        }
                        ColumnLayout {
                            visible: souls.view === "history"; Layout.fillWidth: true; spacing: 12
                            ReadText { text: qsTr("Ogni modifica conserva una revisione. Il ripristino crea una nuova versione, senza riscrivere la cronologia."); Layout.fillWidth: true; color: appearance.colors.muted }
                            Repeater {
                                model: souls.history
                                delegate: ColumnLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    ReadText { text: qsTr("Revisione %1").arg(modelData.revision) + " · " + (modelData.name || ""); Layout.fillWidth: true; font.bold: true }
                                    ReadText { text: modelData.core || ""; Layout.fillWidth: true }
                                    ReadText { text: kformat.timestamp(modelData.created_at) + " · " + (modelData.reason || ""); Layout.fillWidth: true; color: appearance.colors.muted }
                                    KButton { text: qsTr("Ripristina questa versione"); enabled: souls.canEdit && !souls.dirty && modelData.revision !== souls.selected.revision; onClicked: souls.rollback(modelData.revision) }
                                }
                            }
                            KButton { visible: souls.pages.history === true; text: qsTr("Revisioni precedenti"); enabled: !souls.busy; onClicked: souls.more("history") }
                        }
                    }
                }
                ReadText {
                    Layout.fillWidth: true; color: appearance.colors.muted
                    text: !souls.available ? qsTr("Connettiti al servizio per gestire le identità.") : souls.busy ? qsTr("Caricamento…") : !souls.canBind ? qsTr("Per cambiare identità, apri una chat inattiva e metti in pausa il suo obiettivo.") : souls.isNewChat ? qsTr("Sarà usata dal primo messaggio della nuova chat.") : qsTr("L’applicazione è esplicita per questa chat. Il lavoro e i messaggi già presenti restano nella conversazione.")
                }
            }
        }
    }
}
