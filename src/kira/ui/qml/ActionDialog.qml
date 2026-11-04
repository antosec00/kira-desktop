import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
    id: dialog
    objectName: "actionDialog"
    title: qsTr("Omnibox · Progetti, chat, anime e azioni")
    modal: true
    anchors.centerIn: parent
    width: Math.min(750, parent.width - 50)
    height: Math.min(690, parent.height - 50)
    signal projectSelected()
    signal soulRequested(string soulId, string viewMode)
    signal researchRequested(string projectId, string recordId)
    signal canvasRequested(string projectId, string artifactId)
    readonly property color muted: appearance.colors.muted
    readonly property var categoryPills: [
        { id: "all", label: qsTr("Tutto") },
        { id: "chats", label: qsTr("Chat") },
        { id: "souls", label: qsTr("Anime") },
        { id: "knowledge", label: qsTr("Conoscenza") },
        { id: "actions", label: qsTr("Azioni") }
    ]
    background: Rectangle { color: appearance.colors.surface; radius: 12; border.color: appearance.colors.border }
    onOpened: {
        search.text = ""
        workspace.setOmniboxCategory("all")
        workspace.searchActions("")
        search.forceActiveFocus()
    }
    function kindPrefix(kind) {
        if (kind === "project") return "◇  "
        if (kind === "chat") return "💬  "
        if (kind === "soul") return "✦  "
        if (kind === "soul_memory") return "◈  "
        if (kind === "research") return "⌕  "
        if (kind === "canvas") return "▣  "
        return "↗  "
    }
    function kindBadge(kind) {
        if (kind === "project") return qsTr("Progetto")
        if (kind === "chat") return qsTr("Chat")
        if (kind === "soul") return qsTr("Anima")
        if (kind === "soul_memory") return qsTr("Ricordo Anima")
        if (kind === "research") return qsTr("Ricerca")
        if (kind === "canvas") return qsTr("Canvas")
        return qsTr("Azione")
    }
    function activate(item) {
        if (!item || !item.available) return
        if (item.kind === "project") {
            workspace.openProject(item.id)
            projectSelected()
            dialog.close()
        } else if (item.kind === "chat") {
            workspace.openProjectChat(item.project_id || "", item.session_id || "")
            projectSelected()
            dialog.close()
        } else if (item.kind === "soul") {
            soulRequested(item.soul_id || "", "identity")
            dialog.close()
        } else if (item.kind === "soul_memory") {
            soulRequested(item.soul_id || "", "memories")
            dialog.close()
        } else if (item.kind === "research") {
            researchRequested(item.project_id || "", item.record_id || "")
            dialog.close()
        } else if (item.kind === "canvas") {
            canvasRequested(item.project_id || "", item.artifact_id || "")
            dialog.close()
        } else if (item.id === "browser.search") {
            webQuery.forceActiveFocus()
        } else {
            workspace.launchAction(item.id, "")
        }
    }
    ColumnLayout {
        anchors.fill: parent; spacing: 10
        TextField {
            font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
            id: search; objectName: "actionSearch"
            Layout.fillWidth: true; placeholderText: qsTr("Cerca chat, messaggi, anime, ricerche, artefatti o azioni…"); selectByMouse: true
            Accessible.name: qsTr("Cerca progetti e azioni")
            onTextEdited: workspace.searchActions(text)
            Keys.onDownPressed: { actions.currentIndex = 0; actions.forceActiveFocus() }
            onAccepted: if (workspace.desktopActions.length > 0) dialog.activate(workspace.desktopActions[0])
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 6
            Repeater {
                model: dialog.categoryPills
                delegate: KButton {
                    required property var modelData
                    objectName: "omniboxCategory_" + modelData.id
                    text: modelData.label
                    checked: workspace.omniboxCategory === modelData.id
                    quietSelection: true
                    subtle: workspace.omniboxCategory !== modelData.id
                    implicitHeight: 28
                    onClicked: {
                        workspace.setOmniboxCategory(modelData.id)
                        actions.currentIndex = -1
                        search.forceActiveFocus()
                    }
                }
            }
            Item { Layout.fillWidth: true }
        }
        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: workspace.project.name ? qsTr("Progetto attivo: %1 · Ricerca cross-progetto attiva").arg(workspace.project.name) : qsTr("Apri un progetto per le azioni su cartella, terminale e schermate."); color: muted; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
        ReadText { text: workspace.actionError; visible: text.length > 0; color: appearance.colors.warning; Layout.fillWidth: true }
        ListView {
            id: actions; objectName: "desktopActionList"
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 7
            model: workspace.desktopActions; currentIndex: -1
            keyNavigationEnabled: true
            ScrollBar.vertical: ScrollBar {}
            Keys.onReturnPressed: if (currentIndex >= 0) dialog.activate(workspace.desktopActions[currentIndex])
            Keys.onEnterPressed: if (currentIndex >= 0) dialog.activate(workspace.desktopActions[currentIndex])
            delegate: ItemDelegate { HoverHandler { cursorShape: appearance.pointerCursors && parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
                font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
                id: entry
                required property var modelData
                required property int index
                readonly property bool selected: highlighted || hovered || actions.currentIndex === index
                objectName: "desktopAction_" + modelData.id
                width: actions.width; implicitHeight: textColumn.implicitHeight + 22
                enabled: !workspace.nativeBusy
                onClicked: { actions.currentIndex = index; dialog.activate(modelData) }
                Accessible.name: modelData.label + (modelData.available ? "" : ": " + modelData.reason)
                contentItem: ColumnLayout {
                    id: textColumn; spacing: 4
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: dialog.kindPrefix(entry.modelData.kind) + entry.modelData.label; color: entry.selected ? appearance.colors.selectedText : entry.modelData.available ? appearance.colors.text : appearance.colors.muted; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: appearance.uiFont.pixelSize + 1 }
                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: Font.Medium; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: dialog.kindBadge(entry.modelData.kind); color: entry.selected ? appearance.colors.selectedText : muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3) }
                    }
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; text: entry.modelData.available ? entry.modelData.description : (entry.modelData.reason || qsTr("Non disponibile")); color: entry.selected ? appearance.colors.selectedText : muted; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2); textFormat: Text.PlainText }
                }
                background: Rectangle { radius: 8; color: entry.selected ? appearance.colors.selection : appearance.colors.surface; border.color: entry.activeFocus ? appearance.colors.accent : appearance.colors.border }
            }
            Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; textFormat: Text.PlainText; visible: actions.count === 0 && !workspace.actionError; text: qsTr("Nessun risultato. Prova un altro termine."); color: muted }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: appearance.colors.border }
        RowLayout {
            TextField { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; id: webQuery; objectName: "browserQuery"; Layout.fillWidth: true; placeholderText: qsTr("Cerca sul web…"); selectByMouse: true; Accessible.name: qsTr("Testo da cercare sul web"); onAccepted: if (text.trim()) workspace.launchAction("browser.search", text) }
            KButton { objectName: "browserSearchButton"; text: qsTr("Cerca  ↗"); enabled: workspace.connected && !workspace.nativeBusy && webQuery.text.trim().length > 0; onClicked: workspace.launchAction("browser.search", webQuery.text) }
        }
        ReadText { text: workspace.nativeResult; visible: text.length > 0; Layout.fillWidth: true; color: appearance.colors.accent; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
        RowLayout {
            Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("↑ ↓ seleziona · Invio apre · Esc chiude"); color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); Layout.fillWidth: true }
            KButton { text: qsTr("Verifica apertura"); visible: workspace.nativeRetryAvailable; enabled: workspace.connected; onClicked: workspace.retryNativeAction() }
            KButton { text: qsTr("Chiudi"); onClicked: dialog.close() }
        }
    }
}
