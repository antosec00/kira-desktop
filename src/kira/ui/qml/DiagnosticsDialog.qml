import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
    id: dialog
    objectName: "diagnosticsDialog"
    title: qsTr("Diagnostica locale")
    modal: true; anchors.centerIn: parent
    width: Math.min(780, parent.width - 50); height: Math.min(700, parent.height - 50)
    background: Rectangle { color: appearance.colors.surface; radius: 12; border.color: appearance.colors.border }
    onOpened: workspace.refreshDiagnostics()
    ColumnLayout {
        anchors.fill: parent; spacing: 12
        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Versioni, sessione desktop, lavoro attivo e disponibilità delle azioni. Il rapporto esclude account, credenziali, contenuti dei progetti e log grezzi. Puoi leggerlo prima di salvarlo o copiarlo."); color: appearance.colors.muted; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1) }
        ScrollView { id: reportScroll; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth; ReadText { color: appearance.colors.codeText; objectName: "diagnosticsReport"; text: workspace.diagnosticsText; width: reportScroll.availableWidth; font.family: appearance.codeFont.family; font.styleName: appearance.codeFont.styleName; font.weight: appearance.codeFont.weight; font.italic: appearance.codeFont.italic; font.pixelSize: appearance.codeFont.pixelSize } }
        ReadText { text: qsTr("Salvato: %1").arg(workspace.diagnosticsExportPath); visible: workspace.diagnosticsExportPath.length > 0; color: appearance.colors.accent; Layout.fillWidth: true; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
        RowLayout {
            KButton { text: qsTr("Aggiorna"); enabled: workspace.connected; onClicked: workspace.refreshDiagnostics() }
            KButton { text: qsTr("Copia"); enabled: workspace.diagnosticsReady; onClicked: workspace.copy(workspace.diagnosticsText) }
            KButton { objectName: "exportDiagnosticsButton"; text: qsTr("Salva rapporto"); enabled: workspace.diagnosticsReady && workspace.connected; onClicked: workspace.exportDiagnostics() }
            Item { Layout.fillWidth: true }
            KButton { text: qsTr("Chiudi"); onClicked: dialog.close() }
        }
    }
}
