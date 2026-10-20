import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

KiraPanel {
    id: dialog
    objectName: "contextDialog"
    title: qsTr("Contesto e memoria della chat")
    fallbackWidth: 800
    fallbackHeight: 780
    font: appearance.uiFont
    onOpened: { pinInput.text = chatContext.noteDraft; chatContext.open() }
    onClosed: chatContext.hide()
    Connections { target: chatContext; function onDismissed() { dialog.close(); pinInput.text = chatContext.noteDraft } function onPinSaved(content) { if (pinInput.text === content) pinInput.text = "" } }
    footer: RowLayout {
        spacing: 8
        Item { Layout.fillWidth: true }
        KButton { objectName: "contextClose"; text: qsTr("Chiudi"); Layout.rightMargin: 16; Layout.bottomMargin: 12; onClicked: dialog.close() }
    }
    contentItem: ColumnLayout {
        spacing: 10
        RowLayout {
            Layout.fillWidth: true
            ReadText { objectName: "contextMode"; text: workspace.backend === "deepseek_account" ? qsTr("Automatico — compattazione Kira") : chatContext.directContext ? qsTr("Automatico — massimo disponibile") : qsTr("Note e fonti della chat"); Layout.fillWidth: true; color: appearance.colors.accent }
            KButton { objectName: "contextRefresh"; text: qsTr("Aggiorna"); enabled: chatContext.available; onClicked: chatContext.refresh() }
        }
        ReadText { objectName: "contextStatus"; text: chatContext.busy ? qsTr("Preparazione o compattazione in corso…") : chatContext.report.status; Layout.fillWidth: true; color: appearance.colors.muted }
        ReadText { objectName: "contextError"; text: chatContext.error; visible: text.length > 0; Layout.fillWidth: true; color: appearance.colors.warning }
        ScrollView {
            id: scroll; objectName: "contextScroll"
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; contentWidth: availableWidth; rightPadding: 16
            ScrollBar.vertical: KScrollBar { parent: scroll; x: scroll.width - width; y: 0; height: scroll.height; orientation: Qt.Vertical }
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: scroll.availableWidth; spacing: 16
                Rectangle {
                    visible: chatContext.directContext
                    Layout.fillWidth: true; implicitHeight: capacityColumn.implicitHeight + 28
                    color: appearance.colors.surfaceAlt; radius: 12; border.color: appearance.colors.border
                    ColumnLayout {
                        id: capacityColumn; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 14
                        spacing: 8
                        ReadText { text: chatContext.report.capacity.title; Layout.fillWidth: true; font.bold: true; color: appearance.colors.accent }
                        ReadText { objectName: "contextCapacityNote"; text: chatContext.report.capacity.note; Layout.fillWidth: true; color: appearance.colors.muted }
                        Repeater {
                            model: chatContext.report.capacity.rows
                            delegate: RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                ReadText { text: modelData.label; Layout.fillWidth: true }
                                ReadText { text: modelData.value; Layout.preferredWidth: Math.min(145, scroll.availableWidth * 0.42); horizontalAlignment: Text.AlignRight; font.bold: true }
                            }
                        }
                    }
                }
                ReadText { visible: chatContext.directContext; text: qsTr("Compattazione"); font.bold: true; color: appearance.colors.accent; Layout.fillWidth: true }
                ReadText { objectName: "contextCompactionInfo"; visible: chatContext.directContext; text: qsTr("Ultima compattazione: %1\nCompattazioni: %2\nMetodo: %3").arg(chatContext.report.last_compacted).arg(chatContext.report.compaction_count).arg(chatContext.report.method); Layout.fillWidth: true }
                ReadText { visible: chatContext.directContext; text: qsTr("Kira compatta automaticamente prima di esaurire il contesto. La sintesi usa il provider selezionato e può richiedere tempo o quota. I messaggi originali restano conservati."); color: appearance.colors.muted; Layout.fillWidth: true }
                KButton { objectName: "contextCompact"; text: qsTr("Compatta ora"); visible: chatContext.directContext; enabled: chatContext.canEdit; onClicked: chatContext.compact() }
                ReadText { text: qsTr("Note importanti della chat"); font.bold: true; color: appearance.colors.accent; Layout.fillWidth: true }
                ReadText { text: qsTr("Le tue note restano separate dalla sintesi automatica e vengono incluse nel contesto. Conserva qui vincoli e dettagli che vuoi tenere sempre presenti."); color: appearance.colors.muted; Layout.fillWidth: true }
                Repeater {
                    model: chatContext.report.pins
                    delegate: Rectangle {
                        required property var modelData
                        Layout.fillWidth: true; implicitHeight: pinRow.implicitHeight + 20
                        radius: 10; color: appearance.colors.surfaceAlt
                        RowLayout {
                            id: pinRow; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10
                            ReadText { text: modelData.content; Layout.fillWidth: true }
                            KButton { text: qsTr("Rimuovi"); enabled: chatContext.canEdit; onClicked: chatContext.unpin(modelData.id); Layout.alignment: Qt.AlignTop }
                        }
                    }
                }
                TextArea {
                    id: pinInput; objectName: "contextPinInput"
                    onTextChanged: chatContext.setNoteDraft(text)
                    Layout.fillWidth: true; Layout.preferredHeight: Math.max(90, implicitHeight)
                    font: appearance.uiFont; selectByMouse: true; wrapMode: TextEdit.Wrap
                    placeholderText: qsTr("Aggiungi una nota importante…"); Accessible.name: qsTr("Nota importante della chat")
                }
                KButton { objectName: "contextPinSave"; text: qsTr("Conserva nota"); enabled: chatContext.canEdit && pinInput.text.trim().length > 0; onClicked: chatContext.pin(pinInput.text) }
                ReadText { text: qsTr("Memoria generata automaticamente"); font.bold: true; color: appearance.colors.accent; Layout.fillWidth: true }
                ReadText { text: qsTr("La sintesi può perdere dettagli o contenere errori. Le fonti originali permettono di controllare le informazioni; le affermazioni generate non sono fatti verificati."); Layout.fillWidth: true; color: appearance.colors.muted }
                ReadText { objectName: "contextMemoryEmpty"; visible: chatContext.report.sections.length === 0; text: qsTr("Nessuna sintesi della chat disponibile."); Layout.fillWidth: true; color: appearance.colors.muted }
                Repeater {
                    model: chatContext.report.sections
                    delegate: ColumnLayout {
                        required property var modelData
                        Layout.fillWidth: true; spacing: 6
                        ReadText { text: modelData.title; font.bold: true; Layout.fillWidth: true }
                        ReadText { text: modelData.content; Layout.fillWidth: true }
                        Repeater {
                            model: modelData.sources
                            delegate: KButton { required property var modelData; text: qsTr("Leggi fonte: %1").arg(modelData.label); Layout.fillWidth: true; enabled: !chatContext.source.busy; onClicked: chatContext.readSource(modelData.label) }
                        }
                    }
                }
                Repeater {
                    model: chatContext.report.sources
                    delegate: KButton { required property var modelData; text: qsTr("Leggi fonte: %1").arg(modelData.label); Layout.fillWidth: true; enabled: !chatContext.source.busy; onClicked: chatContext.readSource(modelData.label) }
                }
                ColumnLayout {
                    visible: chatContext.source.source_id.length > 0 || chatContext.source.busy
                    Layout.fillWidth: true; spacing: 8
                    ReadText { text: qsTr("Fonte originale: %1").arg(chatContext.source.source_id); font.bold: true; Layout.fillWidth: true; color: appearance.colors.accent }
                    ReadText { objectName: "contextSourceText"; text: chatContext.source.busy ? qsTr("Caricamento…") : chatContext.source.content; Layout.fillWidth: true }
                    RowLayout {
                        KButton { text: qsTr("Pagina precedente"); enabled: chatContext.source.has_previous && !chatContext.source.busy; onClicked: chatContext.previousSourcePage() }
                        KButton { text: qsTr("Pagina successiva"); enabled: chatContext.source.has_more && !chatContext.source.busy; onClicked: chatContext.nextSourcePage() }
                    }
                }
            }
        }
    }
}
