import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
    id: dialog
    objectName: "inputDialog"
    property int pendingAttachments: 0
    title: qsTr("Domande del progetto")
    modal: false
    closePolicy: Popup.CloseOnEscape
    anchors.centerIn: parent
    width: Math.min(680, parent.width - 36)
    height: Math.min(700, parent.height - 36)
    padding: 20
    background: Rectangle { color: appearance.colors.surface; radius: 12; border.color: appearance.colors.border }
    Connections {
        target: inputs
        function onQuestionArrived() { if (!dialog.opened) dialog.open() }
        function onProjectChanged() { dialog.close() }
    }
    contentItem: ColumnLayout {
        spacing: 12
        Text {
            font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic
            Layout.fillWidth: true
            text: inputs.requests.length > 0 ? qsTr("Le domande restano qui anche quando arrivano nuovi aggiornamenti.") : qsTr("Non ci sono domande per questo progetto.")
            color: appearance.colors.muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1); wrapMode: Text.Wrap; textFormat: Text.PlainText
        }
        KComboBox {
            id: requestPicker
            objectName: "inputRequestPicker"
            Layout.fillWidth: true
            visible: inputs.requests.length > 1
            textRole: "label"; valueRole: "key"
            model: inputs.requests.map(function(r, i) {
                var prefix = r.status === "pending" ? qsTr("Da rispondere") : r.status === "closed" ? qsTr("Chiusa") : r.delivered ? qsTr("Inoltrata") : r.received ? qsTr("In inoltro") : qsTr("Salvata")
                return {key: r.key, label: (i + 1) + " · " + prefix + " · " + (r.questions[0].header || r.questions[0].question)}
            })
            currentIndex: inputs.requests.findIndex(function(r) { return r.key === inputs.selectedKey })
            onActivated: inputs.select(currentValue)
            Accessible.name: qsTr("Seleziona la richiesta")
        }
        Text {
            font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic
            Layout.fillWidth: true
            objectName: "inputStatus"
            visible: inputs.requests.length > 0 || !workspace.connected
            text: workspace.connected ? inputs.statusText : qsTr("Servizio disconnesso. La bozza resta disponibile; attendi la riconnessione per inviarla.")
            color: appearance.colors.accent; wrapMode: Text.Wrap; textFormat: Text.PlainText; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1)
        }
        ScrollView {
            id: questionScroll
            objectName: "inputQuestionScroll"
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 0
            implicitHeight: 300
            clip: true; contentWidth: availableWidth
            ColumnLayout {
                width: questionScroll.availableWidth
                spacing: 20
                Repeater {
                    model: inputs.current.questions || []
                    delegate: ColumnLayout {
                        id: questionCard
                        required property var modelData
                        required property int index
                        readonly property var draft: inputs.drafts[modelData.id] || ({})
                        readonly property bool editable: inputs.current.status === "pending" && !inputs.sending
                        readonly property var options: modelData.options || []
                        readonly property bool freeOnly: options.length === 0
                        Layout.fillWidth: true
                        spacing: 8
                        Text {
                            font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.italic: appearance.uiFont.italic
                            Layout.fillWidth: true
                            text: (questionCard.index + 1) + " / " + (inputs.current.questions || []).length + "  ·  " + questionCard.modelData.header
                            font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2); font.weight: Font.Medium; color: appearance.colors.accent
                            wrapMode: Text.Wrap; textFormat: Text.PlainText
                        }
                        ReadText {
                            Layout.fillWidth: true
                            text: questionCard.modelData.question
                            font.pixelSize: appearance.uiFont.pixelSize + 2
                        }
                        Repeater {
                            model: questionCard.options
                            delegate: RadioButton { HoverHandler { cursorShape: appearance.pointerCursors && parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
                                font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
                                id: optionButton
                                required property var modelData
                                required property int index
                                Layout.fillWidth: true
                                objectName: "inputOption_" + questionCard.index + "_" + index
                                checked: questionCard.draft.choice === "option:" + index
                                enabled: questionCard.editable
                                autoExclusive: false
                                text: modelData.label
                                Accessible.name: modelData.label + ". " + (modelData.description || "")
                                onClicked: inputs.setDraft(questionCard.modelData.id, "choice", "option:" + index)
                                contentItem: Column {
                                    leftPadding: optionButton.indicator.width + optionButton.spacing
                                    spacing: 3
                                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; width: parent.width - parent.leftPadding; text: optionButton.modelData.label; color: optionButton.checked ? appearance.colors.selectedText : appearance.colors.text; font.pixelSize: appearance.uiFont.pixelSize; wrapMode: Text.Wrap; textFormat: Text.PlainText }
                                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; width: parent.width - parent.leftPadding; visible: text.length > 0; text: optionButton.modelData.description || ""; color: optionButton.checked ? appearance.colors.selectedText : appearance.colors.muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1); wrapMode: Text.Wrap; textFormat: Text.PlainText }
                                }
                                background: Rectangle { color: optionButton.checked ? appearance.colors.selection : appearance.colors.surfaceAlt; radius: 8; border.width: optionButton.activeFocus ? 2 : 1; border.color: optionButton.activeFocus ? appearance.colors.accent : appearance.colors.border }
                                padding: 10
                            }
                        }
                        RadioButton { HoverHandler { cursorShape: appearance.pointerCursors && parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
                            font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
                            id: otherOption
                            objectName: "inputOther_" + questionCard.index
                            Layout.fillWidth: true
                            visible: Boolean(questionCard.modelData.isOther) && !questionCard.freeOnly
                            text: qsTr("Altro · scrivi la tua risposta")
                            checked: questionCard.draft.choice === "other"
                            enabled: questionCard.editable; autoExclusive: false
                            onClicked: { inputs.setDraft(questionCard.modelData.id, "choice", "other"); otherText.forceActiveFocus() }
                            Accessible.name: text
                        }
                        AttachmentTextArea {
                            id: otherText
                            attachmentContext: JSON.stringify([workspace.project.id || "", inputs.selectedKey, questionCard.modelData.id])
                            onAttachmentBusyChanged: dialog.pendingAttachments += attachmentBusy ? 1 : -1
                            Component.onDestruction: { if (attachmentBusy) dialog.pendingAttachments -= 1 }
                            objectName: "inputText_" + questionCard.index
                            Layout.fillWidth: true; Layout.minimumHeight: 90
                            visible: questionCard.freeOnly || questionCard.draft.choice === "other"
                            enabled: questionCard.editable
                            placeholderText: qsTr("Scrivi una risposta…")
                            Accessible.name: qsTr("Risposta libera: %1").arg(questionCard.modelData.header)
                            text: questionCard.draft.text || ""
                            textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; selectByMouse: true
                            onTextChanged: {
                                if (activeFocus && text !== (questionCard.draft.text || "")) {
                                    inputs.setDraft(questionCard.modelData.id, "text", text)
                                    if (questionCard.freeOnly) inputs.setDraft(questionCard.modelData.id, "choice", "other")
                                }
                            }
                            background: Rectangle { color: appearance.colors.surface; radius: 8; border.color: otherText.activeFocus ? appearance.colors.accent : appearance.colors.border }
                        }
                        KButton {
                            objectName: "inputAttach_" + questionCard.index
                            visible: otherText.visible
                            text: qsTr("Allega"); subtle: true; implicitHeight: 28 + Math.max(0, appearance.uiFont.pixelSize - 13)
                            enabled: questionCard.editable && !otherText.attachmentBusy
                            onClicked: otherText.chooseAttachments()
                            toolTipText: qsTr("Scegli file, oppure incolla o trascina file e immagini nella risposta")
                        }
                        Text {
                            font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic
                            objectName: "inputAttachmentStatus_" + questionCard.index
                            Layout.fillWidth: true
                            visible: otherText.attachmentBusy || otherText.attachmentError.length > 0
                            text: otherText.attachmentBusy ? qsTr("Preparazione allegato…") : otherText.attachmentError
                            color: otherText.attachmentBusy ? appearance.colors.muted : appearance.colors.danger
                            font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2); textFormat: Text.PlainText; wrapMode: Text.Wrap
                        }
                        ReadText {
                            Layout.fillWidth: true
                            visible: inputs.current.status !== "pending"
                            text: {
                                var answer = (inputs.current.answers || {})[questionCard.modelData.id]
                                return answer ? qsTr("Risposta salvata: %1").arg(answer.answers.join("; ")) : qsTr("Nessuna risposta registrata.")
                            }
                            color: appearance.colors.accent; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1)
                        }
                    }
                }
            }
        }
        Text {
            font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
            Layout.fillWidth: true; visible: inputs.error.length > 0
            text: inputs.error; color: appearance.colors.danger; wrapMode: Text.Wrap; textFormat: Text.PlainText
        }
        RowLayout {
            Layout.fillWidth: true
            KButton { objectName: "inputCopy"; text: inputs.current.answers ? qsTr("Copia domanda e risposta") : qsTr("Copia domanda"); subtle: true; onClicked: inputs.copyQuestion() }
            Item { Layout.fillWidth: true }
            KButton { objectName: "inputLater"; text: qsTr("Chiudi"); onClicked: dialog.close(); toolTipText: qsTr("Le domande e la bozza restano nel pannello Domande.") }
            KButton {
                objectName: "inputSubmit"
                text: inputs.sending ? qsTr("Invio…") : qsTr("Invia risposta")
                primary: true
                enabled: workspace.connected && inputs.current.status === "pending" && inputs.complete && !inputs.sending && dialog.pendingAttachments === 0
                onClicked: inputs.submit()
            }
        }
    }
}
