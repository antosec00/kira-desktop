import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: panel
    objectName: "reasoningSummaryPanel"
    property bool expanded: false
    property real maximumBodyHeight: 180
    readonly property var summary: workspace.reasoning.current
    readonly property var history: workspace.reasoning.history
    readonly property bool isStreaming: summary.available === true && summary.partial === true && workspace.busy
    visible: workspace.uiPreferences.show_reasoning_summaries && (summary.available === true || history.length > 0)
    implicitHeight: summaryColumn.implicitHeight + 14
    radius: 10
    color: appearance.colors.surface
    border.width: 1
    border.color: isStreaming
        ? Qt.rgba(appearance.colors.accent.r, appearance.colors.accent.g, appearance.colors.accent.b, 0.45)
        : appearance.colors.border

    Rectangle {
        x: 6; y: 7; width: 2; height: Math.max(0, parent.height - 14); radius: 1
        color: panel.isStreaming ? appearance.colors.accent : appearance.colors.muted
        opacity: panel.isStreaming ? 0.75 : 0.35
    }

    ColumnLayout {
        id: summaryColumn
        anchors.fill: parent
        anchors.leftMargin: 14; anchors.rightMargin: 10
        anchors.topMargin: 7; anchors.bottomMargin: 7
        spacing: 4

        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            StatusIndicator {
                id: reasoningStatus
                objectName: "reasoningSummaryStatusIndicator"
                state: panel.isStreaming ? "running" : (panel.summary.available && panel.summary.text ? "completed" : "idle")
                animate: panel.isStreaming
                diameter: 12
                Layout.alignment: Qt.AlignVCenter
                Accessible.ignored: true
            }
            KButton {
                objectName: "reasoningSummaryToggle"
                Layout.fillWidth: true; Layout.minimumWidth: 0
                text: qsTr("Riepilogo del ragionamento")
                    + (panel.summary.stepTitle ? " · " + panel.summary.stepTitle : "")
                    + (panel.summary.stepCount > 1 ? " (" + panel.summary.stepCount + ")" : "")
                    + (panel.expanded ? " ▴" : " ▾")
                leftAligned: true; subtle: true; implicitHeight: 28 + Math.max(0, appearance.uiFont.pixelSize - 13)
                onClicked: panel.expanded = !panel.expanded
                Accessible.name: text
                toolTipText: qsTr("Riepiloghi pubblici forniti dal provider, distinti dalla risposta")
            }
            KButton {
                visible: panel.expanded
                objectName: "reasoningSummaryHide"
                text: qsTr("Nascondi"); subtle: true
                implicitHeight: 28 + Math.max(0, appearance.uiFont.pixelSize - 13)
                onClicked: workspace.setUiPreference("show_reasoning_summaries", false)
                toolTipText: qsTr("Puoi riattivarli da Impostazioni · Lavoro")
            }
        }
        Text {
            objectName: "reasoningSummaryPreview"
            Layout.fillWidth: true; Layout.minimumWidth: 0
            visible: !panel.expanded && text.length > 0
            text: panel.summary.preview || (panel.summary.text || "").replace(/\s+/g, " ").slice(-400)
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            maximumLineCount: 2
            elide: Text.ElideLeft
            font: appearance.uiFont
            color: panel.isStreaming ? appearance.colors.textSecondary : appearance.colors.muted
        }
        KComboBox {
            objectName: "reasoningSummaryHistory"
            visible: panel.expanded && (panel.history.length > 1 || (panel.history.length > 0 && !panel.summary.available))
            Layout.fillWidth: true; Layout.minimumWidth: 0
            model: panel.history; textRole: "label"; valueRole: "id"
            currentIndex: panel.history.findIndex(function(row) { return row.id === panel.summary.runId })
            onActivated: workspace.reasoning.selectRun(currentValue)
            Accessible.name: qsTr("Riepiloghi delle esecuzioni di questa chat")
        }
        Text {
            objectName: "reasoningSummaryProvenance"
            Layout.fillWidth: true; Layout.minimumWidth: 0
            text: panel.summary.available ? (panel.summary.provider + " · " + panel.summary.status
                  + (panel.summary.historical ? " · " + qsTr("Esecuzione precedente") : ""))
                  : qsTr("Nessun riepilogo ricevuto per questa esecuzione")
            textFormat: Text.PlainText; wrapMode: Text.Wrap
            font.family: appearance.uiFont.family
            font.styleName: appearance.uiFont.styleName
            font.italic: appearance.uiFont.italic
            font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 1)
            color: appearance.colors.muted
        }
        ScrollView {
            id: summaryScroll
            objectName: "reasoningSummaryScroll"
            visible: panel.expanded && !!panel.summary.text
            Layout.fillWidth: true; Layout.minimumWidth: 0
            Layout.preferredHeight: Math.min(panel.maximumBodyHeight, summaryText.implicitHeight + 4)
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ReadText {
                id: summaryText
                objectName: "reasoningSummaryText"
                width: summaryScroll.availableWidth
                text: panel.summary.text || ""
                textFormat: TextEdit.PlainText
                Accessible.name: qsTr("Riepilogo pubblico del provider")
                onTextChanged: {
                    if (panel.expanded && panel.isStreaming && summaryScroll.contentItem
                            && summaryScroll.contentItem.contentHeight > summaryScroll.height) {
                        summaryScroll.contentItem.contentY = Math.max(
                            0, summaryScroll.contentItem.contentHeight - summaryScroll.height)
                    }
                }
            }
        }
        Text {
            Layout.fillWidth: true; Layout.minimumWidth: 0
            visible: panel.expanded && panel.summary.truncated === true
            text: qsTr("Riepilogo limitato; sono mostrati solo i contenuti ricevuti entro il limite.")
            textFormat: Text.PlainText; wrapMode: Text.Wrap
            font: appearance.uiFont; color: appearance.colors.muted
        }
    }
}
