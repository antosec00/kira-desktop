import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    property var images: []
    property bool editable: false
    // Pictures a model produced: shown large, with a way to open the saved file.
    property bool generated: false
    readonly property int previewHeight: 190
    implicitHeight: images.length ? (generated ? previewHeight + 92 + Math.max(0, appearance.uiFont.pixelSize - 13) * 3 : 92) : 0
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
                    readonly property real ratio: modelData.width > 0 && modelData.height > 0 ? modelData.width / modelData.height : 1
                    readonly property int previewWidth: Math.max(150, Math.min(420, Math.round(root.previewHeight * ratio)))
                    width: root.generated ? previewWidth + 12 : 190
                    height: root.generated ? root.implicitHeight - 10 : 82
                    radius: 8
                    color: appearance.colors.surface
                    border.color: appearance.colors.border
                    GridLayout {
                        anchors.fill: parent; anchors.margins: 6
                        columns: root.generated ? 1 : 2; columnSpacing: 6; rowSpacing: 4
                        Image {
                            objectName: "imageThumbnail_" + imageCard.modelData.id
                            source: workspace.imagePreview(imageCard.modelData)
                            asynchronous: true
                            sourceSize.width: root.generated ? 840 : 128; sourceSize.height: root.generated ? 840 : 128
                            Layout.preferredWidth: root.generated ? imageCard.previewWidth : 58
                            Layout.preferredHeight: root.generated ? root.previewHeight : 58
                            fillMode: Image.PreserveAspectFit
                            Accessible.name: qsTr("Anteprima dell'immagine %1").arg(imageCard.modelData.name)
                            TapHandler { enabled: root.generated; onTapped: workspace.openGeneratedImage(imageCard.modelData) }
                            HoverHandler { enabled: root.generated; cursorShape: Qt.PointingHandCursor }
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
                            KButton {
                                objectName: "openImage_" + imageCard.modelData.id
                                visible: root.generated
                                text: qsTr("Apri"); subtle: true
                                implicitHeight: 24 + Math.max(0, appearance.uiFont.pixelSize - 13)
                                onClicked: workspace.openGeneratedImage(imageCard.modelData)
                                Accessible.name: qsTr("Apri l'immagine %1").arg(imageCard.modelData.name)
                            }
                        }
                    }
                }
            }
        }
    }
}
