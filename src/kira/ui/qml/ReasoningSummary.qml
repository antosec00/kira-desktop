import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: panel
    objectName: "reasoningSummaryPanel"
    property bool expanded: false
    property real maximumBodyHeight: 180
    readonly property var summary: workspace.reasoning.current
    readonly property var history: workspace.reasoning.history
    visible: workspace.uiPreferences.show_reasoning_summaries && (summary.available === true || history.length > 0)
    spacing: 4
    RowLayout {
        Layout.fillWidth: true
        KButton {
            objectName: "reasoningSummaryToggle"
            Layout.fillWidth: true; Layout.minimumWidth: 0
            text: qsTr("Riepilogo del ragionamento") + (panel.expanded ? " ▴" : " ▾")
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
        text: (panel.summary.text || "").replace(/\s+/g, " ").slice(-400)
        textFormat: Text.PlainText; maximumLineCount: 1; elide: Text.ElideLeft
        font: appearance.uiFont; color: appearance.colors.muted
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
        font: appearance.uiFont; color: appearance.colors.muted
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
