import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

KiraPanel {
    font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
    id: dialog
    objectName: "workspaceDialog"
    title: qsTr("Riprendi lavoro")
    fallbackWidth: 780
    fallbackHeight: 760
    property bool dirty: false
    property bool loadingForm: false
    property string formProject: ""
    property var formDrafts: ({})
    property string savingForm: ""
    readonly property color muted: appearance.colors.muted
    readonly property color accent: appearance.colors.accent
    KFormat { id: kformat }
    function formKey() { return JSON.stringify([formProject, folder.checked, terminal.checked, editor.checked, nextStep.text]) }
    function rememberForm() {
        if (!formProject || loadingForm) return
        let copy = Object.assign({}, formDrafts)
        copy[formProject] = {dirty: dirty, folder: folder.checked, terminal: terminal.checked,
                             editor: editor.checked, next_step: nextStep.text,
                             watch_path: watchPath.text, tab: tabs.currentIndex}
        formDrafts = copy
    }
    function readForm() {
        if (dirty && formProject === workspace.project.id) return
        let changedProject = formProject !== (workspace.project.id || "")
        if (formProject !== (workspace.project.id || "")) rememberForm()
        loadingForm = true
        formProject = workspace.project.id || ""
        let draft = formDrafts[formProject]
        let tools = workspace.projectWorkspace.tools || []
        folder.checked = draft && draft.dirty ? draft.folder : tools.indexOf("folder") >= 0
        terminal.checked = draft && draft.dirty ? draft.terminal : tools.indexOf("terminal") >= 0
        editor.checked = draft && draft.dirty ? draft.editor : tools.indexOf("editor") >= 0
        nextStep.text = draft && draft.dirty ? draft.next_step : workspace.projectWorkspace.next_step || ""
        if (changedProject) watchPath.text = draft ? draft.watch_path || "" : ""
        if (changedProject) tabs.currentIndex = draft ? draft.tab : 0
        loadingForm = false
        dirty = !!(draft && draft.dirty)
    }
    onOpened: { readForm(); workspace.refreshWorkspace() }
    onClosed: rememberForm()
    Connections {
        target: workspace
        function onWorkspaceChanged() { if (!workspace.workspaceLoading && !workspace.workspaceError) dialog.readForm() }
        function onWorkspaceSaved() {
            if (dialog.savingForm === dialog.formKey()) {
                dialog.dirty = false
                dialog.rememberForm()
                dialog.readForm()
            }
            dialog.savingForm = ""
        }
        function onChanged() {
            if (dialog.formProject && dialog.formProject !== (workspace.project.id || "")) {
                dialog.rememberForm()
                dialog.close()
                dialog.formProject = ""
                dialog.dirty = false
                tabs.currentIndex = 0
            }
        }
    }
    ColumnLayout {
        anchors.fill: parent
        spacing: 12
        ReadText { text: workspace.project.root || ""; color: muted; Layout.fillWidth: true; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; textFormat: Text.PlainText; text: workspace.workspaceError || (workspace.workspaceLoading ? qsTr("Aggiornamento delle prove…") : qsTr("Stato registrato nel progetto")); color: workspace.workspaceError ? appearance.colors.danger : muted; Layout.fillWidth: true; wrapMode: Text.Wrap }
        TabBar {
            id: tabs
            objectName: "workspaceTabs"
            Layout.fillWidth: true
            TabButton { HoverHandler { cursorShape: appearance.pointerCursors && parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }  font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; text: qsTr("Ripresa"); Accessible.name: qsTr("Ripresa del progetto") }
            TabButton { HoverHandler { cursorShape: appearance.pointerCursors && parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }  font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; text: qsTr("File di riferimento"); Accessible.name: qsTr("File e validità delle prove") }
            TabButton { HoverHandler { cursorShape: appearance.pointerCursors && parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }  font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; text: qsTr("Schermate") }
        }
        StackLayout {
            Layout.fillWidth: true; Layout.fillHeight: true
            currentIndex: tabs.currentIndex
            ScrollView {
                id: resumeScroll; clip: true; contentWidth: availableWidth
                ColumnLayout {
                    width: resumeScroll.availableWidth; spacing: 13
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("DA DOVE RIPARTIRE"); color: accent; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); font.letterSpacing: 1 }
                    ReadText { objectName: "resumeSummary"; text: workspace.resumeText; Layout.fillWidth: true }
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Prossimo passo"); color: appearance.colors.text; font.pixelSize: appearance.uiFont.pixelSize + 3 }
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Una nota scritta da te, da rivedere prima di continuare. Le esecuzioni interrotte non vengono ripetute."); color: muted; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1) }
                    ScrollView {
                        Layout.fillWidth: true; Layout.preferredHeight: 94; clip: true
                        TextArea { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; enabled: !workspace.workspaceLoading; background: Rectangle { color: appearance.colors.surface; radius: 7; border.color: nextStep.activeFocus ? appearance.colors.accent : appearance.colors.border } id: nextStep; objectName: "workspaceNextStep"; placeholderText: qsTr("Da dove vuoi continuare?"); wrapMode: TextEdit.Wrap; selectByMouse: true; textFormat: TextEdit.PlainText; Accessible.name: qsTr("Prossimo passo del progetto"); onTextChanged: if (!dialog.loadingForm) dialog.dirty = true }
                    }
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Strumenti da aprire insieme"); color: appearance.colors.text; font.pixelSize: appearance.uiFont.pixelSize + 3 }
                    Flow {
                        Layout.fillWidth: true; spacing: 6
                        CheckBox { HoverHandler { cursorShape: appearance.pointerCursors && parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }  font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; enabled: !workspace.workspaceLoading; id: folder; objectName: "workspaceFolder"; text: qsTr("Cartella"); onClicked: dialog.dirty = true }
                        CheckBox { HoverHandler { cursorShape: appearance.pointerCursors && parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }  font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; enabled: !workspace.workspaceLoading; id: terminal; objectName: "workspaceTerminal"; text: qsTr("Terminale"); onClicked: dialog.dirty = true }
                        CheckBox { HoverHandler { cursorShape: appearance.pointerCursors && parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }  font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; enabled: !workspace.workspaceLoading; id: editor; objectName: "workspaceEditor"; text: qsTr("Editor"); onClicked: dialog.dirty = true }
                    }
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Le applicazioni si aprono nella cartella del progetto. Restano aperte quando chiudi Kira. La disponibilità dipende dal desktop installato."); color: muted; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1) }
                    Flow {
                        Layout.fillWidth: true; spacing: 6
                        KButton { objectName: "saveWorkspaceButton"; text: dialog.dirty ? qsTr("Salva modifiche") : qsTr("Salvato"); enabled: dialog.dirty && workspace.connected && !workspace.workspaceLoading; primary: true; onClicked: { dialog.savingForm = dialog.formKey(); workspace.saveWorkspace(folder.checked, terminal.checked, editor.checked, nextStep.text) } }
                        KButton { objectName: "launchWorkspaceButton"; text: qsTr("Apri strumenti salvati  ↗"); enabled: workspace.connected && !workspace.nativeBusy && !dialog.dirty && !workspace.workspaceLoading; onClicked: workspace.launchWorkspace() }
                    }
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; visible: dialog.dirty; text: qsTr("Salva le modifiche per usare la nuova selezione."); color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
                }
            }
            ColumnLayout {
                spacing: 10
                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Collega una conclusione alla versione di un file. Ricontrolla segnala i cambiamenti; Aggiorna riferimento accetta esplicitamente il nuovo hash."); color: muted; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1) }
                RowLayout {
                    TextField { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; id: watchPath; objectName: "watchPath"; Layout.fillWidth: true; placeholderText: qsTr("Percorso relativo, es. src/main.py"); selectByMouse: true; Accessible.name: qsTr("File di riferimento relativo al progetto"); onAccepted: if (text.trim()) workspace.watchFile(text) }
                    KButton { objectName: "watchFileButton"; text: qsTr("Collega"); enabled: workspace.connected && !workspace.workspaceLoading && watchPath.text.trim().length > 0; onClicked: workspace.watchFile(watchPath.text) }
                }
                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Fino a 64 file regolari di 8 MiB ciascuno, dentro il progetto."); color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); Layout.fillWidth: true; wrapMode: Text.Wrap }
                ListView {
                    id: watchList; objectName: "watchList"
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 10
                    model: workspace.projectWorkspace.watches || []
                    ScrollBar.vertical: ScrollBar {}
                    delegate: Rectangle {
                        required property var modelData
                        width: watchList.width; implicitHeight: watchColumn.implicitHeight + 24; radius: 8; color: appearance.colors.surface; border.color: appearance.colors.border
                        ColumnLayout {
                            id: watchColumn; anchors.fill: parent; anchors.margins: 12; spacing: 6
                            ReadText { text: modelData.path; Layout.fillWidth: true; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1) }
                            Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; textFormat: Text.PlainText; text: ({unchanged: qsTr("Invariato"), changed: qsTr("Modificato · prova da ricontrollare"), missing: qsTr("File mancante"), unavailable: qsTr("Verifica non disponibile")})[modelData.state] || modelData.state; color: modelData.state === "unchanged" ? appearance.colors.success : appearance.colors.warning; Layout.fillWidth: true; wrapMode: Text.Wrap }
                            ReadText { text: modelData.reason || ""; visible: text.length > 0; color: muted; Layout.fillWidth: true; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
                            Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Controllato: %1").arg(modelData.checked_at ? kformat.timestamp(modelData.checked_at) : "—"); color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); Layout.fillWidth: true; elide: Text.ElideRight }
                            Flow {
                                Layout.fillWidth: true; spacing: 6
                                KButton { text: qsTr("Aggiorna riferimento"); implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13); enabled: workspace.connected && !workspace.workspaceLoading; onClicked: workspace.watchFile(modelData.path) }
                                KButton { text: qsTr("Scollega"); subtle: true; implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13); enabled: workspace.connected && !workspace.workspaceLoading; onClicked: workspace.unwatchFile(modelData.path); toolTipText: qsTr("Rimuove il riferimento; il file resta al suo posto.") }
                            }
                        }
                    }
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; textFormat: Text.PlainText; visible: watchList.count === 0; text: qsTr("Nessun file collegato."); color: muted }
                }
            }
            ColumnLayout {
                spacing: 10
                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Acquisisci esplicitamente una schermata e conservala tra le prove del progetto. Il desktop può chiedere di scegliere un'area o una finestra."); color: muted; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1) }
                KButton { objectName: "captureButton"; text: qsTr("Acquisisci schermata…"); enabled: workspace.connected && !workspace.nativeBusy; onClicked: workspace.launchAction("screenshot.capture", "") }
                ListView {
                    id: captures; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 10
                    model: workspace.projectWorkspace.captures || []
                    ScrollBar.vertical: ScrollBar {}
                    delegate: KButton { required property var modelData; width: captures.width; text: "↗  " + kformat.timestamp(modelData.created_at); onClicked: workspace.openArtifact(modelData.path); toolTipText: modelData.path }
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; textFormat: Text.PlainText; visible: captures.count === 0; text: qsTr("Nessuna schermata acquisita."); color: muted }
                }
                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; textFormat: Text.PlainText; visible: Boolean(workspace.projectWorkspace.captures_truncated); text: qsTr("Sono mostrate le 100 schermate più recenti."); color: muted; Layout.fillWidth: true; wrapMode: Text.Wrap }
            }
        }
        ReadText { objectName: "nativeActionResult"; text: workspace.nativeResult; visible: text.length > 0; Layout.fillWidth: true; color: accent; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
        Flow {
            Layout.fillWidth: true; spacing: 6
            KButton { text: qsTr("Ricontrolla"); enabled: workspace.connected && !workspace.workspaceLoading; onClicked: workspace.refreshWorkspace(); toolTipText: qsTr("Rilegge gli esiti e confronta gli hash dei file collegati.") }
            KButton { text: qsTr("Verifica apertura"); visible: workspace.nativeRetryAvailable; enabled: workspace.connected; onClicked: workspace.retryNativeAction() }
            KButton { text: qsTr("Chiudi"); onClicked: dialog.close() }
        }
    }
}
