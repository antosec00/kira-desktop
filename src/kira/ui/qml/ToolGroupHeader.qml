import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

KButton {
    id: group
    property int operationCount: 0
    property bool expanded: false
    property bool needsAttention: false
    property string outcome: "unknown"
    property bool motionEnabled: false
    readonly property string outcomeLabel: outcome === "running" ? qsTr("in corso")
        : outcome === "waiting" ? qsTr("in attesa del browser")
        : outcome === "completed" ? qsTr("completato")
        : outcome === "error" ? qsTr("errore")
        : outcome === "unconfirmed" ? qsTr("esito non confermato")
        : outcome === "paused" ? qsTr("in pausa")
        : outcome === "queued" ? qsTr("in coda")
        : outcome === "cancelled" ? qsTr("annullato") : ""
    readonly property string summary: operationCount === 1 ? qsTr("Attività di Kira · 1 operazione")
        : qsTr("Attività di Kira · %1 operazioni").arg(operationCount)
    text: summary + (outcomeLabel ? " · " + outcomeLabel : "") + (expanded ? "  ▴" : "  ▾")
    iconName: "activity"
    leftAligned: true
    subtle: true
    destructive: needsAttention
    implicitWidth: 320
    implicitHeight: Math.max(34, groupContent.implicitHeight + 14)
    leftPadding: 8; rightPadding: 8; topPadding: 7; bottomPadding: 7
    toolTipText: expanded ? qsTr("Richiudi le operazioni; i risultati originali restano disponibili.")
        : qsTr("Apri le operazioni e consulta risultati, dati e comandi originali.")
    Accessible.name: summary + (outcomeLabel ? " · " + outcomeLabel : "")
    Accessible.description: toolTipText
    contentItem: RowLayout {
        id: groupContent
        spacing: 8
        StatusIndicator {
            id: groupStatus
            objectName: "toolGroupStatus"
            state: group.outcome
            animate: group.motionEnabled
            Layout.preferredWidth: 14; Layout.preferredHeight: 14; diameter: 14
            Accessible.ignored: true
        }
        Text {
            text: group.text; textFormat: Text.PlainText
            font: appearance.uiFont
            color: ["error", "unconfirmed", "waiting"].indexOf(group.outcome) >= 0 ? groupStatus.statusColor : appearance.colors.muted
            Layout.fillWidth: true; Layout.minimumWidth: 0
            wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight
        }
    }
    background: Rectangle {
        radius: 7
        color: group.down ? appearance.colors.pressed : group.hovered ? appearance.colors.hover : "transparent"
        border.width: group.activeFocus ? 2 : 0
        border.color: appearance.colors.accent
    }
}
