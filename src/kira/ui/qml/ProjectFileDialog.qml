import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog; objectName: "projectFileDialog"
    title: qsTr("File del progetto")
    modal: true; anchors.centerIn: parent
    width: Math.min(720, parent.width - 40); height: Math.min(570, parent.height - 60)
    font: appearance.uiFont
    property string initialQuery: ""
    background: Rectangle { color: appearance.colors.surface; radius: 14; border.color: appearance.colors.border }
    onOpened: { search.text = initialQuery; workbench.searchFiles(search.text); search.forceActiveFocus() }
    Timer { id: searchDelay; interval: 180; onTriggered: if (dialog.visible) workbench.searchFiles(search.text) }
    contentItem: ColumnLayout {
        spacing: 10
        Text { text: qsTr("Aggiunge un percorso modificabile al messaggio. Il file viene letto solo quando lo richiedi all’agente."); Layout.fillWidth: true; wrapMode: Text.Wrap; font: appearance.uiFont; color: appearance.colors.muted; textFormat: Text.PlainText }
        TextField {
            id: search; objectName: "projectFileSearch"; Layout.fillWidth: true; font: appearance.uiFont
            selectByMouse: true; placeholderText: qsTr("Cerca per nome o percorso…"); Accessible.name: qsTr("Cerca file del progetto")
            onTextChanged: searchDelay.restart()
            onAccepted: if (fileList.count > 0) { workbench.chooseFile(workbench.files[Math.max(0, fileList.currentIndex)].path); dialog.close() }
            Keys.onDownPressed: fileList.currentIndex = Math.min(fileList.count - 1, fileList.currentIndex + 1)
            Keys.onUpPressed: fileList.currentIndex = Math.max(0, fileList.currentIndex - 1)
        }
        ListView {
            id: fileList; objectName: "projectFileResults"; Layout.fillWidth: true; Layout.fillHeight: true
            model: workbench.files; clip: true; spacing: 4; currentIndex: 0
            ScrollBar.vertical: KScrollBar {}
            onCurrentIndexChanged: positionViewAtIndex(currentIndex, ListView.Contain)
            delegate: KButton {
                required property var modelData; required property int index
                width: fileList.width; leftAligned: true; text: modelData.path; checked: fileList.currentIndex === index
                toolTipText: modelData.path
                onClicked: { workbench.chooseFile(modelData.path); dialog.close() }
            }
        }
        Text { Layout.fillWidth: true; text: workbench.filesLoading ? qsTr("Ricerca…") : workbench.fileNote || (fileList.count ? "" : qsTr("Nessun file trovato.")); wrapMode: Text.Wrap; color: appearance.colors.muted; font: appearance.uiFont; textFormat: Text.PlainText }
        KButton { text: qsTr("Chiudi"); Layout.alignment: Qt.AlignRight; onClicked: dialog.close() }
    }
}
