import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    property var images: []
    property bool editable: false
    implicitHeight: images.length ? 92 : 0
    visible: images.length > 0
    Flickable {
        anchors.fill: parent
        clip: true
        contentWidth: imageRow.implicitWidth
        contentHeight: height
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.horizontal: KScrollBar {}
        Row {
            id: imageRow
            spacing: 8
            Repeater {
                model: root.images
                delegate: Rectangle {
                    id: imageCard
                    required property var modelData
                    width: 190; height: 82; radius: 8
                    color: appearance.colors.surface
                    border.color: appearance.colors.border
                    RowLayout {
                        anchors.fill: parent; anchors.margins: 6; spacing: 6
                        Image {
                            objectName: "imageThumbnail_" + imageCard.modelData.id
                            source: workspace.imagePreview(imageCard.modelData)
                            asynchronous: true
                            sourceSize.width: 128; sourceSize.height: 128
                            Layout.preferredWidth: 58; Layout.preferredHeight: 58
                            fillMode: Image.PreserveAspectFit
                            Accessible.name: qsTr("Anteprima dell'immagine %1").arg(imageCard.modelData.name)
                        }
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 3
                            Text {
                                text: imageCard.modelData.name
                                Layout.fillWidth: true; elide: Text.ElideMiddle
                                color: appearance.colors.text; font: appearance.uiFont; textFormat: Text.PlainText
                            }
                            Text {
                                text: imageCard.modelData.width + " × " + imageCard.modelData.height
                                Layout.fillWidth: true; elide: Text.ElideRight
                                color: appearance.colors.muted; font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 2)
                                font.family: appearance.uiFont.family; textFormat: Text.PlainText
                            }
                            KButton {
                                objectName: "removeImage_" + imageCard.modelData.id
                                visible: root.editable
                                text: qsTr("Rimuovi"); subtle: true
                                implicitHeight: 24 + Math.max(0, appearance.uiFont.pixelSize - 13)
                                onClicked: workspace.removeDraftImage(imageCard.modelData.id)
                                Accessible.name: qsTr("Rimuovi l'immagine %1").arg(imageCard.modelData.name)
                            }
                        }
                    }
                }
            }
        }
    }
}
