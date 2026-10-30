import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog; objectName: "projectFileDialog"
    title: qsTr("File del progetto")
    modal: true; anchors.centerIn: parent
    width: Math.min(720, parent.width - 40); height: Math.min(600, parent.height - 60)
    font: appearance.uiFont
    property string initialQuery: ""
    signal mentionSelected(string kind, string value, string token)
    background: Rectangle { color: appearance.colors.surface; radius: 14; border.color: appearance.colors.border }
    onOpened: {
        search.text = initialQuery
        mentions.searchUniversal(search.text)
        workbench.searchFiles(search.text)
        search.forceActiveFocus()
    }
    Timer {
        id: searchDelay; interval: 180
        onTriggered: if (dialog.visible) {
            mentions.searchUniversal(search.text)
            workbench.searchFiles(search.text)
        }
    }
    contentItem: ColumnLayout {
        spacing: 10
        Text {
            text: qsTr("Aggiunge un percorso modificabile o un contesto @Mention (@git, @terminal, @web, @soul, @folder, @file) al messaggio.")
            Layout.fillWidth: true; wrapMode: Text.Wrap
            font: appearance.uiFont; color: appearance.colors.muted; textFormat: Text.PlainText
        }
        TextField {
            id: search; objectName: "projectFileSearch"; Layout.fillWidth: true; font: appearance.uiFont
            selectByMouse: true
            placeholderText: qsTr("Cerca file o digita @git, @terminal, @web, @soul, @folder…")
            Accessible.name: qsTr("Cerca file del progetto")
            onTextChanged: {
                mentions.searchUniversal(text)
                searchDelay.restart()
            }
            onAccepted: {
                if (search.text.trim().indexOf("@") === 0 && mentions.universalItems.length > 0) {
                    var m = mentions.universalItems[0]
                    dialog.mentionSelected(m.kind, m.value, m.token)
                    dialog.close()
                } else if (fileList.count > 0) {
                    workbench.chooseFile(workbench.files[Math.max(0, fileList.currentIndex)].path)
                    dialog.close()
                } else if (mentions.universalItems.length > 0) {
                    var fallback = mentions.universalItems[0]
                    dialog.mentionSelected(fallback.kind, fallback.value, fallback.token)
                    dialog.close()
                }
            }
            Keys.onDownPressed: fileList.currentIndex = Math.min(fileList.count - 1, fileList.currentIndex + 1)
            Keys.onUpPressed: fileList.currentIndex = Math.max(0, fileList.currentIndex - 1)
        }
        ColumnLayout {
            objectName: "universalMentionsSection"
            visible: mentions.universalItems.length > 0
            Layout.fillWidth: true
            spacing: 6
            Text {
                text: qsTr("@MENTIONS CONTESTUALI")
                font.family: appearance.uiFont.family
                font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 3)
                font.bold: true
                color: appearance.colors.muted
                textFormat: Text.PlainText
            }
            Flow {
                objectName: "universalMentionsFlow"
                Layout.fillWidth: true
                spacing: 6
                Repeater {
                    model: mentions.universalItems.slice(0, 8)
                    delegate: KButton {
                        required property var modelData
                        required property int index
                        objectName: "universalMentionChip_" + index
                        subtle: true
                        cornerRadius: 12
                        implicitHeight: 28
                        text: modelData.label
                        toolTipText: modelData.description
                        onClicked: {
                            dialog.mentionSelected(modelData.kind, modelData.value, modelData.token)
                            dialog.close()
                        }
                    }
                }
            }
        }
        Text {
            visible: fileList.count > 0
            text: qsTr("FILE DEL PROGETTO")
            font.family: appearance.uiFont.family
            font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 3)
            font.bold: true
            color: appearance.colors.muted
            textFormat: Text.PlainText
        }
        ListView {
            id: fileList; objectName: "projectFileResults"; Layout.fillWidth: true; Layout.fillHeight: true
            model: workbench.files; clip: true; spacing: 4; currentIndex: 0
            ScrollBar.vertical: KScrollBar {}
            onCurrentIndexChanged: positionViewAtIndex(currentIndex, ListView.Contain)
            delegate: RowLayout {
                required property var modelData; required property int index
                width: fileList.width; spacing: 6
                KButton {
                    Layout.fillWidth: true; leftAligned: true; text: modelData.path; checked: fileList.currentIndex === index
                    toolTipText: modelData.path
                    onClicked: { workbench.chooseFile(modelData.path); dialog.close() }
                }
                KButton {
                    objectName: "attachFileMentionChip_" + index
                    subtle: true; cornerRadius: 10; implicitHeight: 28
                    text: "@file"
                    toolTipText: qsTr("Allega come chip @file con contenuto iniettato")
                    onClicked: {
                        dialog.mentionSelected("file", modelData.path, "@file:" + modelData.path)
                        dialog.close()
                    }
                }
            }
        }
        Text { Layout.fillWidth: true; text: workbench.filesLoading ? qsTr("Ricerca…") : workbench.fileNote || (fileList.count ? "" : qsTr("Nessun file trovato.")); wrapMode: Text.Wrap; color: appearance.colors.muted; font: appearance.uiFont; textFormat: Text.PlainText }
        KButton { text: qsTr("Chiudi"); Layout.alignment: Qt.AlignRight; onClicked: dialog.close() }
    }
}
