import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Button {
    id: receipt
    property string operationLabel: ""
    property string target: ""
    property string errorPreview: ""
    property string outcome: "unknown"
    property bool motionEnabled: false
    property bool expanded: false
    readonly property bool needsAttention: outcome === "error" || outcome === "unconfirmed"
    readonly property string outcomeLabel: outcome === "running" ? qsTr("In esecuzione")
        : outcome === "waiting" ? qsTr("Attende il browser")
        : outcome === "completed" ? qsTr("Confermato")
        : outcome === "error" ? qsTr("Non riuscita")
        : outcome === "unconfirmed" ? qsTr("Esito non confermato")
        : outcome === "paused" ? qsTr("In pausa")
        : outcome === "queued" ? qsTr("In coda")
        : outcome === "cancelled" ? qsTr("Annullato") : qsTr("Esito sconosciuto")
    readonly property string description: expanded ? qsTr("Richiudi l’output originale")
        : qsTr("Apri l’output originale e i comandi di copia")
    text: operationLabel
    implicitWidth: 320
    implicitHeight: receiptContent.implicitHeight + topPadding + bottomPadding
    leftPadding: 26; rightPadding: 9; topPadding: 9; bottomPadding: 9
    hoverEnabled: true
    font: appearance.uiFont
    Accessible.name: [operationLabel, target, outcomeLabel, errorPreview].filter(function(part) { return part.length > 0 }).join(" · ")
    Accessible.description: description
    HoverHandler { cursorShape: appearance.pointerCursors && receipt.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
    KToolTip { visible: receipt.hovered; text: receipt.description }
    background: Rectangle {
        radius: 7
        color: receipt.down ? appearance.colors.pressed : receipt.hovered ? appearance.colors.hover
            : receipt.outcome === "running" ? appearance.colors.surface : "transparent"
        border.width: receipt.activeFocus ? 2 : 0
        border.color: appearance.colors.accent
        Rectangle {
            x: 8; y: 0; width: 1; height: parent.height
            color: appearance.colors.accent; opacity: 0.35
        }
        StatusIndicator {
            id: receiptStatus
            objectName: "toolReceiptStatus"
            x: 2; y: receipt.topPadding + Math.max(0, (appearance.uiFont.pixelSize - diameter) / 2) + 2
            state: receipt.outcome
            animate: receipt.motionEnabled
            Accessible.ignored: true
        }
    }
    contentItem: RowLayout {
        id: receiptContent
        spacing: 8
        ColumnLayout {
            Layout.fillWidth: true; Layout.minimumWidth: 0
            spacing: 4
            GridLayout {
                Layout.fillWidth: true
                id: titleRow
                columns: width < 420 + Math.max(0, appearance.uiFont.pixelSize - 13) * 16 ? 1 : 3
                columnSpacing: 12; rowSpacing: 3
                Text {
                    objectName: "toolReceiptLabel"
                    Layout.fillWidth: titleRow.columns === 1; Layout.minimumWidth: 0
                    Layout.maximumWidth: titleRow.columns === 1 ? titleRow.width
                        : Math.max(40, titleRow.width - outcomeText.implicitWidth - titleRow.columnSpacing * 2)
                    text: receipt.operationLabel; textFormat: Text.PlainText
                    font: appearance.uiFont; color: appearance.colors.text
                    wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight
                }
                Text {
                    id: outcomeText
                    objectName: "toolReceiptOutcome"
                    Layout.fillWidth: parent.columns === 1; Layout.minimumWidth: 0
                    text: receipt.outcomeLabel; textFormat: Text.PlainText
                    font: appearance.uiFont
                    color: receiptStatus.statusColor
                    wrapMode: Text.Wrap
                }
                Item { visible: titleRow.columns === 3; Layout.fillWidth: true; Layout.minimumWidth: 0 }
            }
            Text {
                objectName: "toolReceiptTarget"
                Layout.fillWidth: true; Layout.minimumWidth: 0
                visible: text.length > 0
                text: receipt.target; textFormat: Text.PlainText
                font.family: appearance.codeFont.family
                font.styleName: appearance.codeFont.styleName
                font.pixelSize: Math.max(11, appearance.uiFont.pixelSize - 1)
                color: appearance.colors.muted
                wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideMiddle
            }
            Text {
                objectName: "toolReceiptError"
                Layout.fillWidth: true; Layout.minimumWidth: 0
                visible: text.length > 0
                text: receipt.errorPreview; textFormat: Text.PlainText
                font: appearance.uiFont; color: receipt.outcome === "error" ? appearance.colors.danger : appearance.colors.warning
                wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight
            }
        }
        Text {
            text: receipt.expanded ? "⌃" : "⌄"; textFormat: Text.PlainText
            font: appearance.uiFont; color: appearance.colors.muted
            Layout.alignment: Qt.AlignTop
            Accessible.ignored: true
        }
    }
}
