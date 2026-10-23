import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: panel
    objectName: "chatRunError"
    property var failure: ({visible: false})
    signal detailsRequested(string details)
    visible: failure.visible === true
    implicitHeight: errorColumn.implicitHeight + 18
    color: appearance.colors.surfaceAlt
    radius: 12
    border.color: appearance.colors.warning
    ColumnLayout {
        id: errorColumn
        anchors.fill: parent; anchors.margins: 9
        spacing: 4
        RowLayout {
            Layout.fillWidth: true
            Text {
                objectName: "chatRunErrorTitle"
                Layout.fillWidth: true; Layout.minimumWidth: 0
                text: panel.failure.title || ""
                textFormat: Text.PlainText; elide: Text.ElideRight
                color: appearance.colors.warning
                font.family: appearance.uiFont.family
                font.styleName: appearance.uiFont.styleName
                font.italic: appearance.uiFont.italic
                font.pixelSize: appearance.uiFont.pixelSize
                font.weight: Math.max(Font.DemiBold, appearance.uiFont.weight)
            }
            KButton {
                objectName: "chatRunErrorDetails"
                text: qsTr("Dettagli"); subtle: true
                implicitHeight: 28 + Math.max(0, appearance.uiFont.pixelSize - 13)
                Accessible.name: qsTr("Apri dettagli esecuzione")
                onClicked: panel.detailsRequested(panel.failure.details || "")
            }
        }
        Text {
            objectName: "chatRunErrorSummary"
            Layout.fillWidth: true; Layout.minimumWidth: 0
            visible: text.length > 0
            text: panel.failure.summary || ""
            textFormat: Text.PlainText; wrapMode: Text.Wrap
            maximumLineCount: 2; elide: Text.ElideRight
            font: appearance.uiFont; color: appearance.colors.text
        }
        Text {
            objectName: "chatRunErrorCause"
            Layout.fillWidth: true; Layout.minimumWidth: 0
            visible: text.length > 0
            text: panel.failure.errorPreview || ""
            textFormat: Text.PlainText; wrapMode: Text.Wrap
            maximumLineCount: 2; elide: Text.ElideRight
            font: appearance.uiFont; color: appearance.colors.text
        }
        Text {
            objectName: "chatRunErrorRequest"
            Layout.fillWidth: true; Layout.minimumWidth: 0
            visible: (panel.failure.requestExcerpt || "").length > 0
            text: qsTr("Richiesta: %1").arg(panel.failure.requestExcerpt || "")
            textFormat: Text.PlainText; elide: Text.ElideRight
            font.family: appearance.uiFont.family; color: appearance.colors.muted
            font.styleName: appearance.uiFont.styleName
            font.italic: appearance.uiFont.italic
            font.weight: appearance.uiFont.weight
            font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2)
        }
    }
}
