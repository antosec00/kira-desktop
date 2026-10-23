import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "actionPalette"
    title: qsTr("Azioni rapide")
    modal: true
    anchors.centerIn: parent
    width: Math.min(650, parent.width - 40)
    height: Math.min(570, parent.height - 60)
    font: appearance.uiFont
    property string initialQuery: ""
    signal selected(string action)
    background: Rectangle { color: appearance.colors.surface; radius: 14; border.color: appearance.colors.border }
    function search() {
        choices.model = workbench.actions(query.text)
        choices.currentIndex = 0
    }
    function activate() {
        if (choices.currentIndex < 0 || choices.currentIndex >= choices.count) return
        var entry = choices.model[choices.currentIndex]
        if (entry.enabled) { dialog.selected(entry.id); dialog.close() }
    }
    onOpened: { query.text = initialQuery; search(); query.forceActiveFocus() }
    Connections { target: localization; function onChanged() { dialog.search() } }
    contentItem: ColumnLayout {
        spacing: 10
        TextField {
            id: query; objectName: "quickActionSearch"
            Layout.fillWidth: true; font: appearance.uiFont; selectByMouse: true
            placeholderText: qsTr("Cerca un’azione o scrivi /model, /plan, /diff…")
            Accessible.name: qsTr("Cerca azioni rapide")
            onTextChanged: dialog.search()
            Keys.onDownPressed: choices.currentIndex = Math.min(choices.count - 1, choices.currentIndex + 1)
            Keys.onUpPressed: choices.currentIndex = Math.max(0, choices.currentIndex - 1)
            onAccepted: dialog.activate()
        }
        ListView {
            id: choices; objectName: "actionChoices"
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 4
            ScrollBar.vertical: KScrollBar {}
            keyNavigationEnabled: true
            onCurrentIndexChanged: positionViewAtIndex(currentIndex, ListView.Contain)
            Keys.onReturnPressed: dialog.activate()
            delegate: KButton {
                required property var modelData
                required property int index
                width: choices.width; implicitHeight: 64
                enabled: modelData.enabled; checked: choices.currentIndex === index
                text: modelData.label; Accessible.description: modelData.description
                contentItem: Column {
                    padding: 10; spacing: 3
                    Text { width: parent.width - 20; text: modelData.label; color: parent.parent.foreground; font: appearance.uiFont; textFormat: Text.PlainText; elide: Text.ElideRight }
                    Text { width: parent.width - 20; text: modelData.description; color: parent.parent.checked ? parent.parent.foreground : appearance.colors.muted; font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 2); textFormat: Text.PlainText; elide: Text.ElideRight }
                }
                onClicked: { dialog.selected(modelData.id); dialog.close() }
            }
        }
        Text { visible: choices.count === 0; text: qsTr("Nessuna azione corrispondente."); color: appearance.colors.muted; font: appearance.uiFont; textFormat: Text.PlainText }
        RowLayout {
            Text { Layout.fillWidth: true; text: qsTr("↑ ↓ per scegliere · Invio per aprire · Esc per chiudere"); wrapMode: Text.Wrap; color: appearance.colors.muted; font: appearance.uiFont; textFormat: Text.PlainText }
            KButton { text: qsTr("Chiudi"); onClicked: dialog.close() }
        }
    }
}
