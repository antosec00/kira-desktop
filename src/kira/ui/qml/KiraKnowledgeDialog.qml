import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

KiraPanel {
    id: dialog
    objectName: "assistantEnvironmentDialog"
    title: qsTr("Kira · Conoscenza e strumenti")
    fallbackWidth: 900
    fallbackHeight: 800
    font: appearance.uiFont
    property bool showRaw: false
    property bool showCapabilities: false
    signal memoryRequested()
    signal researchRequested()
    onOpened: knowledge.open()
    onClosed: knowledge.hide()
    KFormat { id: kformat }
    Connections { target: knowledge; function onDismissed() { dialog.showRaw = false; dialog.showCapabilities = false; dialog.close() } }
    footer: Flow {
        padding: 12; spacing: 6
        KButton { objectName: "knowledgeMemory"; text: dialog.compactPanel ? qsTr("Contesto") : qsTr("Contesto e memoria"); toolTipText: qsTr("Contesto e memoria"); onClicked: { dialog.close(); dialog.memoryRequested() } }
        KButton { objectName: "knowledgeResearch"; text: dialog.compactPanel ? qsTr("Ricerca") : qsTr("Ricerca del progetto"); toolTipText: qsTr("Ricerca del progetto"); onClicked: { dialog.close(); dialog.researchRequested() } }
        KButton { objectName: "knowledgeClose"; text: qsTr("Chiudi"); onClicked: dialog.close() }
    }
    contentItem: ColumnLayout {
        spacing: 8
        ReadText {
            objectName: "knowledgeIdentity"
            text: qsTr("Kira · assistente del progetto")
            font.bold: true; color: appearance.colors.accent; Layout.fillWidth: true
        }
        Label {
            objectName: "knowledgeModel"
            text: qsTr("Modello: %1").arg(knowledge.summary.model) + " · " + kformat.providerLabel(knowledge.summary.backend)
            font: appearance.uiFont; color: appearance.colors.muted
            textFormat: Text.PlainText; elide: Text.ElideRight; Layout.fillWidth: true; Layout.minimumWidth: 0
        }
        Flow {
            Layout.fillWidth: true
            spacing: 6
            KButton { objectName: "knowledgeTab"; text: qsTr("Conoscenza"); checked: knowledge.view === "knowledge"; enabled: knowledge.available && !knowledge.busy; onClicked: knowledge.setView("knowledge") }
            KButton { objectName: "knowledgeToolsTab"; text: qsTr("Strumenti del progetto"); checked: knowledge.view === "tools"; enabled: knowledge.available && !knowledge.busy; onClicked: knowledge.setView("tools") }
            KButton { objectName: "knowledgeRefresh"; text: dialog.compactPanel ? "↻" : qsTr("Aggiorna"); toolTipText: qsTr("Aggiorna"); Accessible.name: qsTr("Aggiorna"); enabled: knowledge.available && !knowledge.busy; onClicked: knowledge.refresh() }
        }
        GridLayout {
            Layout.fillWidth: true
            columns: 3
            TextField {
                objectName: "knowledgeQuery"; Layout.fillWidth: true; Layout.minimumWidth: 60
                font: appearance.uiFont; selectByMouse: true; maximumLength: 200
                enabled: knowledge.available
                placeholderText: knowledge.view === "tools" ? qsTr("Cerca uno strumento…") : qsTr("Cerca nella guida e nella memoria…")
                Accessible.name: placeholderText
                text: knowledge.query; onTextEdited: knowledge.setQuery(text)
                onAccepted: knowledge.search()
            }
            KComboBox {
                objectName: "knowledgeScope"; visible: knowledge.view === "knowledge"
                Layout.preferredWidth: dialog.compactPanel ? 130 : 160; Layout.minimumWidth: 100
                model: knowledge.scopes; textRole: "label"; valueRole: "value"
                currentIndex: indexOfValue(knowledge.scope); enabled: knowledge.available && !knowledge.busy
                onActivated: knowledge.setScope(currentValue)
                Accessible.name: qsTr("Dove cercare")
            }
            KButton { objectName: "knowledgeSearch"; text: qsTr("Cerca"); enabled: knowledge.available && !knowledge.busy; onClicked: knowledge.search() }
        }
        ReadText { objectName: "knowledgeError"; text: knowledge.error; visible: text.length > 0; Layout.fillWidth: true; color: appearance.colors.warning }
        RowLayout {
            visible: knowledge.pendingCount > 0; Layout.fillWidth: true
            ReadText { text: qsTr("Richieste da verificare: %1").arg(knowledge.pendingCount); Layout.fillWidth: true; color: appearance.colors.warning }
            KButton { objectName: "knowledgeRetry"; text: qsTr("Riprova la stessa richiesta"); enabled: !knowledge.busy; onClicked: knowledge.retryPending() }
        }
        ReadText { objectName: "knowledgeStatus"; text: !knowledge.available ? qsTr("Apri una chat del progetto e connettiti al servizio per consultare Kira.") : knowledge.busy ? qsTr("Caricamento…") : qsTr("La consultazione non avvia il modello né esegue strumenti."); Layout.fillWidth: true; color: appearance.colors.muted }
        ScrollView {
            id: scroll; objectName: "knowledgeScroll"
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; contentWidth: availableWidth; rightPadding: 16
            ScrollBar.vertical: KScrollBar { parent: scroll; x: scroll.width - width; y: 0; height: scroll.height; orientation: Qt.Vertical }
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: scroll.availableWidth; spacing: 10
                ColumnLayout {
                    visible: knowledge.view === "knowledge" && !knowledge.source.ref.length
                    Layout.fillWidth: true; spacing: 10
                    Repeater {
                        model: knowledge.summary.anchors
                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true; implicitHeight: anchorColumn.implicitHeight + 20
                            color: appearance.colors.surfaceAlt; radius: 10
                            ColumnLayout {
                                id: anchorColumn; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10
                                RowLayout {
                                    Layout.fillWidth: true
                                    ReadText { text: modelData.label; font.bold: true; Layout.fillWidth: true }
                                    KButton { text: qsTr("Leggi"); subtle: true; visible: modelData.ref.length > 0; enabled: !knowledge.busy; onClicked: knowledge.readSource(modelData.ref) }
                                }
                                Text { text: modelData.excerpt || modelData.title; font: appearance.uiFont; color: appearance.colors.text; textFormat: Text.PlainText; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; Layout.fillWidth: true }
                            }
                        }
                    }
                    Flow {
                        Layout.fillWidth: true; spacing: 6
                        KButton { objectName: "knowledgeCapabilities"; text: qsTr("Capacità della chat"); checked: dialog.showCapabilities; onClicked: dialog.showCapabilities = !dialog.showCapabilities }
                        KButton { objectName: "knowledgeNotes"; text: qsTr("Note importanti: %1").arg(knowledge.summary.notes_count); visible: knowledge.summary.notes_ref.length > 0; enabled: !knowledge.busy; onClicked: knowledge.readSource(knowledge.summary.notes_ref) }
                        KButton { text: qsTr("Sintesi della chat"); visible: knowledge.summary.memory_ref.length > 0; enabled: !knowledge.busy; onClicked: knowledge.readSource(knowledge.summary.memory_ref) }
                    }
                    Repeater {
                        model: dialog.showCapabilities ? knowledge.capabilities : []
                        delegate: ColumnLayout {
                            required property var modelData
                            Layout.fillWidth: true; spacing: 3
                            ReadText { text: modelData.label + " · " + modelData.status; font.bold: true; Layout.fillWidth: true }
                            ReadText { text: modelData.reason; visible: text.length > 0; Layout.fillWidth: true; color: appearance.colors.muted }
                            ReadText { text: modelData.transport; Layout.fillWidth: true; color: appearance.colors.muted }
                            KButton { text: qsTr("Leggi dettagli"); visible: modelData.ref.length > 0; enabled: !knowledge.busy; onClicked: knowledge.readSource(modelData.ref) }
                        }
                    }
                    Repeater {
                        model: knowledge.items
                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true; implicitHeight: resultColumn.implicitHeight + 20
                            radius: 10; color: appearance.colors.surfaceAlt
                            ColumnLayout {
                                id: resultColumn; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10
                                ReadText { text: modelData.title; font.bold: true; Layout.fillWidth: true }
                                ReadText { text: modelData.summary; visible: text.length > 0; Layout.fillWidth: true; color: appearance.colors.muted }
                                ReadText { text: modelData.sharing_label; visible: text.length > 0; Layout.fillWidth: true; color: appearance.colors.muted }
                                ReadText { text: modelData.provenance; visible: text.length > 0; Layout.fillWidth: true; color: appearance.colors.muted }
                                KButton { text: qsTr("Apri fonte"); enabled: !knowledge.busy && modelData.available && modelData.ref.length > 0; onClicked: knowledge.readSource(modelData.ref) }
                            }
                        }
                    }
                    ReadText { text: qsTr("Nessun risultato disponibile. Prova una ricerca diversa."); visible: knowledge.available && !knowledge.busy && knowledge.items.length === 0; Layout.fillWidth: true; color: appearance.colors.muted }
                }
                ColumnLayout {
                    visible: knowledge.view === "knowledge" && knowledge.source.ref.length > 0
                    Layout.fillWidth: true; spacing: 8
                    KButton { objectName: "knowledgeSourceBack"; text: qsTr("← Risultati"); enabled: !knowledge.busy; onClicked: knowledge.back() }
                    ReadText { objectName: "knowledgeSourceTitle"; text: knowledge.source.title; font.bold: true; Layout.fillWidth: true }
                    ReadText { text: qsTr("Revisione %1").arg(knowledge.source.revision); visible: knowledge.source.revision.length > 0; Layout.fillWidth: true; color: appearance.colors.muted }
                    ReadText { text: knowledge.source.provenance; visible: text.length > 0; Layout.fillWidth: true; color: appearance.colors.muted }
                    ReadText { objectName: "assistantEnvironmentText"; text: knowledge.source.content; Layout.fillWidth: true }
                    ReadText { text: qsTr("Contenuto limitato dalla fonte o dalla pagina corrente."); visible: knowledge.source.truncated; Layout.fillWidth: true; color: appearance.colors.warning }
                    Flow {
                        Layout.fillWidth: true; spacing: 6
                        KButton { objectName: "knowledgeSourcePrevious"; text: qsTr("Pagina precedente"); enabled: knowledge.source.has_previous && !knowledge.busy; onClicked: knowledge.previousSourcePage() }
                        KButton { objectName: "knowledgeSourceNext"; text: qsTr("Pagina successiva"); enabled: knowledge.source.has_more && !knowledge.busy; onClicked: knowledge.nextSourcePage() }
                    }
                }
                ColumnLayout {
                    visible: knowledge.view === "tools" && !knowledge.selectedTool.id
                    Layout.fillWidth: true; spacing: 10
                    ReadText { text: qsTr("Gli strumenti appartengono a questo progetto. Creazione e prove si richiedono nella chat."); Layout.fillWidth: true; color: appearance.colors.muted }
                    Repeater {
                        model: knowledge.tools
                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true; implicitHeight: toolColumn.implicitHeight + 20
                            radius: 10; color: appearance.colors.surfaceAlt
                            ColumnLayout {
                                id: toolColumn; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10
                                ReadText { text: modelData.name; font.bold: true; Layout.fillWidth: true }
                                ReadText { text: qsTr("Revisione %1").arg(modelData.revision) + " · " + modelData.status_label; Layout.fillWidth: true; color: appearance.colors.accent }
                                ReadText { text: modelData.preview; Layout.fillWidth: true; color: appearance.colors.muted }
                                KButton { text: qsTr("Dettagli"); enabled: !knowledge.busy; onClicked: knowledge.readTool(modelData.id, 0) }
                            }
                        }
                    }
                    ReadText { text: qsTr("Nessuno strumento registrato. Puoi chiedere a Kira di prepararne uno nella chat."); visible: knowledge.available && !knowledge.busy && knowledge.tools.length === 0; Layout.fillWidth: true; color: appearance.colors.muted }
                }
                ColumnLayout {
                    visible: knowledge.view === "tools" && !!knowledge.selectedTool.id
                    Layout.fillWidth: true; spacing: 10
                    KButton { text: qsTr("← Strumenti"); enabled: !knowledge.busy; onClicked: knowledge.back() }
                    ReadText { objectName: "knowledgeToolName"; text: knowledge.selectedTool.name || ""; font.bold: true; Layout.fillWidth: true }
                    ReadText { objectName: "knowledgeToolRevision"; text: qsTr("Revisione %1").arg(knowledge.selectedTool.revision || ""); Layout.fillWidth: true; color: appearance.colors.muted }
                    ReadText { objectName: "knowledgeToolStatus"; text: knowledge.selectedTool.status_label || ""; color: appearance.colors.accent; Layout.fillWidth: true }
                    ReadText { text: knowledge.selectedTool.description || ""; Layout.fillWidth: true }
                    ReadText { objectName: "knowledgeToolTest"; text: (knowledge.selectedTool.test_label || "") + (knowledge.selectedTool.test_detail ? " · " + knowledge.selectedTool.test_detail : ""); Layout.fillWidth: true }
                    ReadText { text: qsTr("Una prova superata riguarda questa revisione e i casi dichiarati; non conferma ogni possibile effetto esterno."); Layout.fillWidth: true; color: appearance.colors.muted }
                    Flow {
                        objectName: "knowledgeToolActions"; Layout.fillWidth: true; spacing: 6
                        KButton { objectName: "knowledgeUseTool"; text: qsTr("Usa nella chat"); enabled: knowledge.canInsert; onClicked: { knowledge.insertTool(false); dialog.close() } }
                        KButton { objectName: "knowledgeTestTool"; text: qsTr("Chiedi una prova"); enabled: knowledge.canInsert; onClicked: { knowledge.insertTool(true); dialog.close() } }
                        KButton { objectName: "knowledgeConfigureTool"; text: knowledge.selectedTool.enabled ? qsTr("Disattiva") : qsTr("Abilita"); enabled: knowledge.canConfigure; onClicked: knowledge.configureTool(!knowledge.selectedTool.enabled) }
                        KButton { objectName: "knowledgeToolHistory"; text: qsTr("Versioni"); enabled: !knowledge.busy; onClicked: knowledge.showHistory() }
                        KButton { text: qsTr("Contratto dello strumento"); checked: dialog.showRaw; onClicked: dialog.showRaw = !dialog.showRaw }
                    }
                    ReadText { text: qsTr("Il riferimento viene aggiunto alla bozza: l’invio resta tuo. Prove ed esecuzione richiedono Esegui e Host fidato o Accesso completo."); Layout.fillWidth: true; color: appearance.colors.muted }
                    ReadText { text: qsTr("Disattivare blocca gli usi futuri; non annulla le attività già avviate."); Layout.fillWidth: true; color: appearance.colors.muted }
                    ReadText { objectName: "knowledgeToolContract"; visible: dialog.showRaw; text: knowledge.selectedTool.contract || ""; font: appearance.codeFont; Layout.fillWidth: true }
                    Repeater {
                        model: knowledge.history.items
                        delegate: KButton {
                            required property var modelData
                            text: qsTr("Revisione %1").arg(modelData.revision) + " · " + modelData.test_label
                            Layout.fillWidth: true; enabled: !knowledge.busy
                            onClicked: knowledge.readTool(modelData.id, modelData.revision)
                        }
                    }
                    Flow {
                        visible: knowledge.history.items.length > 0; Layout.fillWidth: true; spacing: 6
                        KButton { text: qsTr("Pagina precedente"); enabled: knowledge.history.has_previous && !knowledge.busy; onClicked: knowledge.previousHistoryPage() }
                        KButton { text: qsTr("Pagina successiva"); enabled: knowledge.history.has_more && !knowledge.busy; onClicked: knowledge.nextHistoryPage() }
                    }
                }
            }
        }
        RowLayout {
            visible: !knowledge.source.ref.length && !knowledge.selectedTool.id
            Layout.fillWidth: true
            KButton { objectName: "knowledgePrevious"; text: qsTr("Pagina precedente"); enabled: knowledge.page.has_previous && !knowledge.busy; onClicked: knowledge.previousPage() }
            Item { Layout.fillWidth: true }
            KButton { objectName: "knowledgeNext"; text: qsTr("Pagina successiva"); enabled: knowledge.page.has_more && !knowledge.busy; onClicked: knowledge.nextPage() }
        }
    }
}
