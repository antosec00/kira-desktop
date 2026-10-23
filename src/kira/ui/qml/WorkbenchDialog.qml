import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog; objectName: "workbenchDialog"
    title: qsTr("Strumenti della chat")
    modal: true; anchors.centerIn: parent
    width: Math.min(900, parent.width - 40); height: Math.min(720, parent.height - 50)
    font: appearance.uiFont
    property string section: "queue"
    property string contextStamp: ""
    property var sections: ["queue", "history", "chat", "archives", "changes", "goal"]
    function activate(name) {
        section = name
        if (name === "queue") workbench.refreshQueue()
        else if (name === "history") workbench.searchHistory(historySearch.text)
        else if (name === "archives") workbench.refreshArchives()
        else if (name === "changes") workbench.refreshChanges("")
        else if (name === "goal") workbench.refreshGoal()
    }
    function confirmCompact() { compactDialog.open() }
    KFormat { id: kformat }
    function openSection(name) { section = name; open() }
    onOpened: { contextStamp = JSON.stringify([workspace.project.id || "", workspace.sessionId]); activate(section) }
    Connections {
        target: workspace
        function onChatChanged() { if (dialog.visible && dialog.contextStamp !== JSON.stringify([workspace.project.id || "", workspace.sessionId])) dialog.close() }
    }
    Connections { target: workbench; function onGoalChanged() { if (!goalText.activeFocus) goalText.text = workbench.goal.objective || "" } function onOperationCompleted(method) { if (method === "queue.edit") queueEditor.close() } }
    background: Rectangle { color: appearance.colors.surface; radius: 14; border.color: appearance.colors.border }
    contentItem: ColumnLayout {
        spacing: 10
        KComboBox {
            objectName: "workbenchSection"; Layout.fillWidth: true
            model: [qsTr("Coda del progetto"), qsTr("Cronologia dei messaggi inviati"), qsTr("Gestisci chat"), qsTr("Chat archiviate"), qsTr("Modifiche"), qsTr("Obiettivo della chat")]
            currentIndex: Math.max(0, dialog.sections.indexOf(dialog.section))
            Accessible.name: qsTr("Sezione degli strumenti")
            onActivated: dialog.activate(dialog.sections[index])
        }
        Text { visible: workbench.error.length > 0; text: workbench.error; Layout.fillWidth: true; wrapMode: Text.Wrap; font: appearance.uiFont; color: appearance.colors.warning; textFormat: Text.PlainText }
        StackLayout {
            Layout.fillWidth: true; Layout.fillHeight: true
            currentIndex: dialog.sections.indexOf(dialog.section)
            ColumnLayout {
                spacing: 10
                Text { text: qsTr("La coda prosegue dopo un lavoro completato. Dopo errori, arresti o riavvio serve Riprendi. Mettere in pausa non ferma il lavoro già avviato."); Layout.fillWidth: true; wrapMode: Text.Wrap; font: appearance.uiFont; color: appearance.colors.muted; textFormat: Text.PlainText }
                RowLayout {
                    Text { Layout.fillWidth: true; text: workbench.queuePaused ? qsTr("Coda in pausa") : qsTr("Coda attiva"); color: appearance.colors.text; font: appearance.uiFont; textFormat: Text.PlainText }
                    KButton { objectName: "queuePauseResume"; text: workbench.queuePaused ? qsTr("Riprendi coda") : qsTr("Pausa coda"); enabled: workspace.connected && !workbench.loading; onClicked: workbench.setQueuePaused(!workbench.queuePaused) }
                    KButton { text: qsTr("Aggiorna"); enabled: workspace.connected; onClicked: workbench.refreshQueue() }
                }
                ListView {
                    id: queueList; objectName: "queueList"; Layout.fillWidth: true; Layout.fillHeight: true
                    model: workbench.queueItems; clip: true; spacing: 8
                    ScrollBar.vertical: KScrollBar {}
                    delegate: Rectangle {
                        required property var modelData
                        width: queueList.width; implicitHeight: queueRow.implicitHeight + 20
                        color: appearance.colors.surfaceAlt; border.color: appearance.colors.border; radius: 10
                        ColumnLayout {
                            id: queueRow; anchors.fill: parent; anchors.margins: 10; spacing: 6
                            Text { text: modelData.modeLabel + " · " + modelData.label; Layout.fillWidth: true; wrapMode: Text.Wrap; font: appearance.uiFont; color: appearance.colors.accent; textFormat: Text.PlainText }
                            Text { text: modelData.prompt; Layout.fillWidth: true; wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight; font: appearance.uiFont; color: appearance.colors.text; textFormat: Text.PlainText }
                            ImageAttachments { objectName: "queueImages_" + modelData.item_id; images: modelData.attachments || []; Layout.fillWidth: true; Layout.preferredHeight: implicitHeight }
                            Text { visible: Boolean(modelData.error); text: modelData.error || ""; Layout.fillWidth: true; wrapMode: Text.Wrap; font: appearance.uiFont; color: appearance.colors.warning; textFormat: Text.PlainText }
                            RowLayout {
                                KButton { text: qsTr("Apri chat"); subtle: true; onClicked: { workspace.selectSession(modelData.session_id); dialog.close() } }
                                KButton { text: modelData.status === "pending" ? qsTr("Modifica") : qsTr("Leggi"); onClicked: { queueEditor.itemId = modelData.item_id; queueEditor.editable = modelData.status === "pending"; queuePrompt.text = modelData.prompt; queueEditor.images = modelData.attachments || []; queueEditor.open() } }
                                Item { Layout.fillWidth: true }
                                KButton { text: "↑"; Accessible.name: qsTr("Sposta prima"); visible: modelData.status === "pending"; enabled: !workbench.loading; onClicked: workbench.moveQueue(modelData.item_id, -1) }
                                KButton { text: "↓"; Accessible.name: qsTr("Sposta dopo"); visible: modelData.status === "pending"; enabled: !workbench.loading; onClicked: workbench.moveQueue(modelData.item_id, 1) }
                                KButton { text: qsTr("Rimuovi"); visible: modelData.status !== "running" && modelData.status !== "starting"; enabled: !workbench.loading; onClicked: workbench.removeQueue(modelData.item_id) }
                            }
                        }
                    }
                    Text { anchors.centerIn: parent; visible: queueList.count === 0; text: qsTr("Nessuna richiesta in coda."); font: appearance.uiFont; color: appearance.colors.muted; textFormat: Text.PlainText }
                }
            }
            ColumnLayout {
                Text { text: qsTr("Ultimi 200 messaggi confermati dal servizio, conservati privatamente. Riutilizza aggiunge il testo alla bozza; non lo invia."); Layout.fillWidth: true; wrapMode: Text.Wrap; font: appearance.uiFont; color: appearance.colors.muted; textFormat: Text.PlainText }
                TextField { id: historySearch; objectName: "promptHistorySearch"; Layout.fillWidth: true; font: appearance.uiFont; selectByMouse: true; placeholderText: qsTr("Cerca nei messaggi inviati…"); onTextChanged: workbench.searchHistory(text) }
                ListView {
                    id: historyList; objectName: "promptHistoryList"; Layout.fillWidth: true; Layout.fillHeight: true
                    model: workbench.history; spacing: 8; clip: true; ScrollBar.vertical: KScrollBar {}
                    delegate: Rectangle {
                        required property var modelData
                        width: historyList.width; implicitHeight: historyRow.implicitHeight + 20
                        color: appearance.colors.surfaceAlt; radius: 10
                        ColumnLayout {
                            id: historyRow; anchors.fill: parent; anchors.margins: 10
                            Text { objectName: "promptHistoryTime"; text: kformat.timestamp(modelData.timestamp); font: appearance.uiFont; color: appearance.colors.muted; textFormat: Text.PlainText }
                            Text { text: modelData.text; Layout.fillWidth: true; wrapMode: Text.Wrap; maximumLineCount: 4; elide: Text.ElideRight; font: appearance.uiFont; color: appearance.colors.text; textFormat: Text.PlainText }
                            KButton { text: qsTr("Riutilizza nella bozza"); Layout.alignment: Qt.AlignRight; onClicked: { workbench.useHistory(modelData.text); dialog.close() } }
                        }
                    }
                    Text { anchors.centerIn: parent; visible: historyList.count === 0; text: qsTr("Nessun messaggio trovato."); font: appearance.uiFont; color: appearance.colors.muted; textFormat: Text.PlainText }
                }
                KButton { text: qsTr("Svuota cronologia locale del progetto"); enabled: historyList.count > 0; onClicked: clearHistoryDialog.open() }
            }
            ScrollView {
                id: chatScroll; contentWidth: availableWidth; clip: true
                ColumnLayout {
                    width: chatScroll.availableWidth; spacing: 14
                    Text { text: workspace.sessionTitle; font: appearance.uiFont; color: appearance.colors.text; wrapMode: Text.Wrap; Layout.fillWidth: true; textFormat: Text.PlainText }
                    Text { text: qsTr("Una diramazione continua da un punto completato e conserva la chat originale. Non annulla le modifiche ai file."); font: appearance.uiFont; color: appearance.colors.muted; wrapMode: Text.Wrap; Layout.fillWidth: true; textFormat: Text.PlainText }
                    KComboBox { id: forkRun; objectName: "forkRunChoice"; Layout.fillWidth: true; model: [{id: "", label: qsTr("Fine della chat corrente")}].concat(workbench.completedRuns); textRole: "label"; valueRole: "id"; currentIndex: 0; Accessible.name: qsTr("Continua dopo questa richiesta completata") }
                    Text { text: qsTr("Per le chat meno recenti è disponibile la fine della chat. I punti intermedi richiedono un confine del turno registrato dal provider."); font: appearance.uiFont; color: appearance.colors.muted; wrapMode: Text.Wrap; Layout.fillWidth: true; textFormat: Text.PlainText }
                    KButton { objectName: "forkChatButton"; text: qsTr("Continua da qui"); enabled: workbench.canFork && forkRun.count > 0; onClicked: workbench.fork(forkRun.currentValue) }
                    KButton { text: qsTr("Esporta chat in Markdown"); enabled: workspace.connected && workspace.sessionId.length > 0; onClicked: chatExport.open() }
                    KButton { text: qsTr("Copia ultima risposta"); onClicked: workbench.copyLastOutput() }
                    Text { visible: workbench.hasUncertainRequests; text: qsTr("Esistono richieste senza conferma finale. Ripetere la stessa azione riusa la sua identità; verifica chat, coda e attività prima di iniziare una richiesta separata."); font: appearance.uiFont; color: appearance.colors.warning; wrapMode: Text.Wrap; Layout.fillWidth: true; textFormat: Text.PlainText }
                    KButton { visible: workbench.hasUncertainRequests; text: qsTr("Consenti nuove richieste separate…"); enabled: !workbench.loading; onClicked: newIntentDialog.open() }
                    KButton { objectName: "archiveChatButton"; text: workbench.sessionArchived ? qsTr("Ripristina chat") : qsTr("Archivia chat"); enabled: workspace.connected && workspace.sessionId.length > 0 && !workspace.busy && !teams.busy && !workbench.loading; onClicked: workbench.archive(workspace.sessionId, !workbench.sessionArchived) }
                    Text { text: qsTr("Archiviare nasconde la chat dall’elenco principale e conserva la cronologia. Puoi ripristinarla in Chat archiviate."); font: appearance.uiFont; color: appearance.colors.muted; wrapMode: Text.Wrap; Layout.fillWidth: true; textFormat: Text.PlainText }
                    // Direct providers compact in the context panel; Codex compacts natively.
                    KButton { objectName: "compactContextButton"; text: chatContext.directContext ? qsTr("Contesto e memoria") : qsTr("Riduci il contesto"); enabled: chatContext.directContext ? chatContext.available : workbench.canCompact; onClicked: { if (chatContext.directContext) { dialog.close(); workbench.invokeAction("context") } else compactDialog.open() } }
                    Text { objectName: "compactUnavailableNote"; visible: !chatContext.directContext && !workbench.nativeCompactionAvailable; text: qsTr("La riduzione del contesto non è dichiarata disponibile dal provider selezionato."); font: appearance.uiFont; color: appearance.colors.muted; wrapMode: Text.Wrap; Layout.fillWidth: true; textFormat: Text.PlainText }
                    Text { text: qsTr("Riepiloga produce una risposta leggibile. Riduci il contesto modifica il contesto del provider; la cronologia di Kira resta conservata. Disponibilità dipendente dal provider."); font: appearance.uiFont; color: appearance.colors.muted; wrapMode: Text.Wrap; Layout.fillWidth: true; textFormat: Text.PlainText }
                }
            }
            ColumnLayout {
                KButton { text: qsTr("Aggiorna"); enabled: workspace.connected && !workbench.loading; onClicked: workbench.refreshArchives() }
                ListView {
                    id: archiveList; objectName: "archivedChatsList"; Layout.fillWidth: true; Layout.fillHeight: true
                    model: workbench.archives; spacing: 8; clip: true; ScrollBar.vertical: KScrollBar {}
                    delegate: RowLayout {
                        required property var modelData
                        width: archiveList.width
                        Text { text: modelData.label; font: appearance.uiFont; color: appearance.colors.text; elide: Text.ElideRight; Layout.fillWidth: true; textFormat: Text.PlainText }
                        KButton { text: qsTr("Apri"); onClicked: { workspace.selectSession(modelData.id); dialog.close() } }
                        KButton { text: qsTr("Ripristina"); enabled: !workbench.loading; onClicked: workbench.archive(modelData.id, false) }
                    }
                    Text { anchors.centerIn: parent; visible: archiveList.count === 0; text: qsTr("Nessuna chat archiviata."); font: appearance.uiFont; color: appearance.colors.muted; textFormat: Text.PlainText }
                }
                KButton { visible: workbench.archivesHasMore; text: qsTr("Carica altre chat"); enabled: !workbench.loading; onClicked: workbench.loadMoreArchives() }
            }
            ColumnLayout {
                RowLayout {
                    KButton { text: qsTr("Aggiorna"); enabled: workspace.connected && !workbench.loading; onClicked: workbench.refreshChanges("") }
                    KButton { text: qsTr("Richiedi revisione"); enabled: !workspace.busy && !teams.busy; onClicked: { workbench.setMode("review"); workbench.useHistory(qsTr("Esamina le modifiche del progetto e segnala problemi concreti, senza modificare file.")); dialog.close() } }
                    Item { Layout.fillWidth: true }
                }
                Text { visible: workbench.changes.available === false; text: workbench.changes.reason || qsTr("Le differenze Git non sono disponibili per questo progetto."); font: appearance.uiFont; color: appearance.colors.muted; wrapMode: Text.Wrap; Layout.fillWidth: true; textFormat: Text.PlainText }
                KComboBox {
                    id: changedFile; objectName: "changedFileChoice"; Layout.fillWidth: true
                    model: [{path: "", label: qsTr("Tutte le modifiche")}].concat((workbench.changes.files || []).map(function(file) { return {path: file.path, label: file.status + "  " + file.path} }))
                    textRole: "label"; valueRole: "path"; Accessible.name: qsTr("File da confrontare")
                    onActivated: workbench.refreshChanges(currentValue)
                }
                ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; ReadText { objectName: "projectDiffText"; width: parent.width; text: workbench.changes.diff || qsTr("Nessuna differenza da mostrare."); font: appearance.codeFont; wrapMode: TextEdit.NoWrap } }
                Text { visible: Boolean(workbench.changes.truncated); text: qsTr("Differenze limitate per dimensione. Seleziona un file per approfondire."); font: appearance.uiFont; color: appearance.colors.warning; wrapMode: Text.Wrap; Layout.fillWidth: true; textFormat: Text.PlainText }
            }
            ColumnLayout {
                Text { text: qsTr("Kira prosegue per cicli fino all’obiettivo, senza un limite predefinito. Mantiene un riepilogo e le evidenze; si ferma se serve il tuo intervento. Il completamento è una valutazione del modello."); font: appearance.uiFont; color: appearance.colors.muted; wrapMode: Text.Wrap; Layout.fillWidth: true; textFormat: Text.PlainText }
                Text { objectName: "autonomousGoalStatus"; text: workbench.goalStatusLabel; font: appearance.uiFont; color: appearance.colors.accent; wrapMode: Text.Wrap; Layout.fillWidth: true; textFormat: Text.PlainText }
                Text { visible: text.length > 0; text: workbench.goalCycleLabel; font: appearance.uiFont; color: appearance.colors.muted; Layout.fillWidth: true; textFormat: Text.PlainText }
                ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 90; clip: true; TextArea { id: goalText; objectName: "goalObjective"; width: parent.width; font: appearance.uiFont; wrapMode: TextEdit.Wrap; selectByMouse: true; textFormat: TextEdit.PlainText; placeholderText: qsTr("Descrivi il risultato che vuoi ottenere…"); readOnly: workbench.autonomousGoal.status === "active" } }
                ScrollView { visible: workbench.goalProgress.length > 0; Layout.fillWidth: true; Layout.preferredHeight: visible ? Math.min(150, goalProgress.implicitHeight) : 0; clip: true; ReadText { id: goalProgress; objectName: "autonomousGoalProgress"; width: parent.width; text: workbench.goalProgress; font: appearance.uiFont; wrapMode: TextEdit.Wrap } }
                Text { objectName: "autonomousGoalStopReason"; visible: text.length > 0; text: workbench.goalStopReason; font: appearance.uiFont; color: appearance.colors.warning; wrapMode: Text.Wrap; Layout.fillWidth: true; maximumLineCount: 4; elide: Text.ElideRight; textFormat: Text.PlainText; KToolTip { visible: reasonHover.hovered; text: parent.text } HoverHandler { id: reasonHover } }
                Flow {
                    Layout.fillWidth: true; spacing: 8
                    KButton { objectName: "startAutonomousGoalButton"; text: qsTr("Avvia autonomamente"); primary: true; visible: !workbench.autonomousGoal.id || workbench.autonomousGoal.status === "completed"; enabled: workspace.connected && goalText.text.trim().length > 0 && !workbench.loading && !workbench.sessionArchived; onClicked: workbench.startAutonomousGoal(goalText.text) }
                    KButton { objectName: "pauseAutonomousGoalButton"; text: qsTr("Pausa obiettivo"); visible: workbench.autonomousGoal.status === "active"; enabled: workspace.connected && !workbench.loading; toolTipText: qsTr("Impedisce nuovi cicli. Lo strumento in corso può terminare."); onClicked: workbench.pauseGoal() }
                    KButton { objectName: "resumeAutonomousGoalButton"; text: qsTr("Riprendi autonomamente"); primary: true; visible: !!workbench.autonomousGoal.id && ["paused", "blocked", "interrupted"].indexOf(workbench.autonomousGoal.status) >= 0; enabled: workspace.connected && !workbench.loading && !workbench.sessionArchived; onClicked: workbench.resumeGoal() }
                    KButton { objectName: "saveGoalButton"; text: qsTr("Salva senza avviare"); visible: !workbench.autonomousGoal.id; enabled: workspace.sessionId.length > 0 && goalText.text.trim().length > 0 && !workspace.busy && !teams.busy && !workbench.loading; onClicked: workbench.saveGoal(goalText.text, "active") }
                    KButton { text: workbench.goal.status === "paused" ? qsTr("Riprendi obiettivo") : qsTr("Pausa obiettivo"); visible: !workbench.autonomousGoal.id; enabled: Boolean(workbench.goal.objective) && !workspace.busy && !teams.busy && !workbench.loading; onClicked: workbench.saveGoal(workbench.goal.objective, workbench.goal.status === "paused" ? "active" : "paused") }
                    KButton { text: qsTr("Segna completato"); visible: !workbench.autonomousGoal.id; enabled: Boolean(workbench.goal.objective) && !workspace.busy && !teams.busy && !workbench.loading; onClicked: workbench.saveGoal(workbench.goal.objective, "completed") }
                    KButton { objectName: "continueGoalButton"; text: qsTr("Continua con un passo"); visible: !workbench.autonomousGoal.id; enabled: workbench.goal.status === "active" && !workspace.busy && !teams.busy && !workbench.loading; onClicked: { workbench.continueGoal(); dialog.close() } }
                }
                Text { text: qsTr("Comandi: /obiettivo risultato · /obiettivo pausa · /obiettivo riprendi · /obiettivo stato"); font: appearance.uiFont; color: appearance.colors.muted; wrapMode: Text.Wrap; Layout.fillWidth: true; textFormat: Text.PlainText }
            }
        }
        KButton { text: qsTr("Chiudi"); Layout.alignment: Qt.AlignRight; onClicked: dialog.close() }
    }
    Dialog {
        id: queueEditor; objectName: "queueEditor"; title: editable ? qsTr("Modifica richiesta in coda") : qsTr("Richiesta in coda")
        property string itemId: ""; property bool editable: false; property var images: []
        modal: true; anchors.centerIn: parent; width: Math.min(650, dialog.width - 30); height: Math.min(430, dialog.height - 30)
        background: Rectangle { color: appearance.colors.surface; radius: 12; border.color: appearance.colors.border }
        contentItem: ColumnLayout {
            Text { visible: workbench.error.length > 0; text: workbench.error; Layout.fillWidth: true; wrapMode: Text.Wrap; font: appearance.uiFont; color: appearance.colors.warning; textFormat: Text.PlainText }
            ImageAttachments { images: queueEditor.images; Layout.fillWidth: true; Layout.preferredHeight: implicitHeight }
            ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; TextArea { id: queuePrompt; objectName: "queuePromptEditor"; width: parent.width; font: appearance.uiFont; wrapMode: TextEdit.Wrap; selectByMouse: true; readOnly: !queueEditor.editable; textFormat: TextEdit.PlainText } }
            RowLayout {
                Item { Layout.fillWidth: true }
                KButton { text: qsTr("Annulla"); onClicked: queueEditor.close() }
                KButton { text: qsTr("Salva"); visible: queueEditor.editable; enabled: (queuePrompt.text.trim().length > 0 || queueEditor.images.length > 0) && !workbench.loading; onClicked: workbench.editQueue(queueEditor.itemId, queuePrompt.text) }
            }
        }
    }
    Dialog {
        id: clearHistoryDialog; title: qsTr("Svuotare la cronologia locale?"); modal: true; anchors.centerIn: parent
        width: Math.min(500, dialog.width - 30); implicitHeight: 240; height: implicitHeight; standardButtons: Dialog.Cancel | Dialog.Ok
        background: Rectangle { color: appearance.colors.surface; radius: 12; border.color: appearance.colors.border }
        contentItem: Text { text: qsTr("Rimuove solo l’elenco locale dei messaggi riutilizzabili di questo progetto. Le conversazioni e le bozze restano conservate."); wrapMode: Text.Wrap; font: appearance.uiFont; color: appearance.colors.text; textFormat: Text.PlainText }
        onAccepted: workbench.clearHistory()
    }
    Dialog {
        id: compactDialog; objectName: "compactContextDialog"; title: qsTr("Ridurre il contesto del provider?"); modal: true; anchors.centerIn: parent
        width: Math.min(530, dialog.width - 30); implicitHeight: 270; height: implicitHeight; standardButtons: Dialog.Cancel | Dialog.Ok
        background: Rectangle { color: appearance.colors.surface; radius: 12; border.color: appearance.colors.border }
        contentItem: Text { text: qsTr("Il provider riassumerà il proprio contesto e potrà omettere dettagli nelle risposte future. La cronologia salvata da Kira non viene eliminata. Questa operazione è distinta da Riepiloga."); wrapMode: Text.Wrap; font: appearance.uiFont; color: appearance.colors.text; textFormat: Text.PlainText }
        onAccepted: workbench.compact()
    }
    Dialog {
        id: newIntentDialog; objectName: "newIntentDialog"; title: qsTr("Iniziare richieste separate?"); modal: true; anchors.centerIn: parent
        width: Math.min(530, dialog.width - 30); implicitHeight: 270; height: implicitHeight
        standardButtons: Dialog.Cancel | Dialog.Ok
        background: Rectangle { color: appearance.colors.surface; radius: 12; border.color: appearance.colors.border }
        contentItem: Text { text: qsTr("Le operazioni precedenti potrebbero essere già riuscite. Conferma solo dopo aver controllato chat, coda e attività: inviare nuovamente può duplicare una richiesta o una diramazione. Questa conferma non invia nulla."); wrapMode: Text.Wrap; font: appearance.uiFont; color: appearance.colors.text; textFormat: Text.PlainText }
        onAccepted: workbench.allowNewRequests()
    }
    KiraFileDialog { id: chatExport; objectName: "chatMarkdownExport"; mode: "save"; purpose: "export"; title: qsTr("Esporta chat"); nameFilters: [qsTr("Markdown (*.md)")]; defaultSuffix: "md"; suggestedName: ((workspace.project.name || "Kira") + " - " + qsTr("chat")).replace(/[\\/:*?"<>|]+/g, "-"); onAccepted: workbench.exportChat(selectedFile.toString()) }
}
