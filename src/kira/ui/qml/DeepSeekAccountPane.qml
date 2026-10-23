import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: pane
    Layout.fillWidth: true
    implicitHeight: content.implicitHeight + 32
    radius: 12
    color: appearance.colors.surfaceAlt
    border.color: appearance.colors.border
    // An unsaved checkbox choice (null follows the saved mode). It lasts until the
    // saved mode matches it; hiding the pane discards it.
    property var pendingProxy: null
    readonly property bool proxySelected: pendingProxy === null ? deepseekAccount.proxyEnabled : pendingProxy
    onVisibleChanged: if (!visible) pendingProxy = null
    Connections {
        target: deepseekAccount
        function onChanged() { if (pane.pendingProxy === deepseekAccount.proxyEnabled) pane.pendingProxy = null }
    }

    ColumnLayout {
        id: content
        anchors.fill: parent
        anchors.margins: 16
        spacing: 10
        ReadText { text: qsTr("DeepSeek · metodo di accesso"); color: appearance.colors.text; font.bold: true; Layout.fillWidth: true }
        ReadText { text: qsTr("Scegli l’account DeepSeek oppure un’istanza locale DeeperSeeker. Gli strumenti e i permessi restano gestiti da Kira."); Layout.fillWidth: true; color: appearance.colors.muted }

        CheckBox {
            id: useProxy
            objectName: "deepseekUseProxy"
            text: qsTr("Usa DeeperSeeker")
            checked: pane.proxySelected
            enabled: workspace.connected && !deepseekAccount.busy
            onToggled: pane.pendingProxy = checked
        }
        ColumnLayout {
            visible: useProxy.checked
            Layout.fillWidth: true
            Label { text: qsTr("Indirizzo DeeperSeeker") }
            TextField {
                id: proxyUrl
                objectName: "deepseekProxyUrl"
                text: deepseekAccount.proxyUrl
                placeholderText: "http://127.0.0.1:4000"
                selectByMouse: true
                Layout.fillWidth: true
            }
            Label { text: qsTr("Chiave API DeeperSeeker") }
            TextField {
                id: proxyKey
                objectName: "deepseekProxyKey"
                echoMode: TextInput.Password
                placeholderText: deepseekAccount.proxyConfigured ? qsTr("Configurata; lascia vuoto per mantenerla") : qsTr("Inserisci la chiave API")
                selectByMouse: true
                Layout.fillWidth: true
            }
            ReadText { text: qsTr("La chiave viene conservata nel profilo privato Kira e non viene mostrata dopo il salvataggio."); Layout.fillWidth: true; color: appearance.colors.muted }
            KButton {
                objectName: "deepseekProxySave"
                text: qsTr("Salva connessione DeeperSeeker")
                primary: true
                enabled: workspace.connected && !deepseekAccount.busy
                onClicked: {
                    deepseekAccount.configureProxy(proxyUrl.text, proxyKey.text, useProxy.checked)
                    proxyKey.clear()
                }
            }
        }

        // Switching back to the account keeps the saved proxy key for later use.
        KButton {
            objectName: "deepseekProxyDisable"
            text: qsTr("Disattiva DeeperSeeker")
            primary: true
            visible: !useProxy.checked && deepseekAccount.proxyEnabled
            enabled: workspace.connected && !deepseekAccount.busy
            onClicked: deepseekAccount.configureProxy(deepseekAccount.proxyUrl, "", false)
        }
        Rectangle { visible: !useProxy.checked; Layout.fillWidth: true; implicitHeight: 1; color: appearance.colors.border }
        ReadText { visible: !useProxy.checked; text: qsTr("Kira usa il flusso account PKCE del DeepSeek Harness e conserva il grant nel profilo locale."); Layout.fillWidth: true; color: appearance.colors.muted }
        ReadText { visible: !useProxy.checked; text: qsTr("Nota: il logout in Kira rimuove il grant locale, ma potrebbe non revocare un’eventuale chiave gestita da DSH sulla piattaforma."); Layout.fillWidth: true; color: appearance.colors.warning }
        ReadText { objectName: "deepseekAccountStatus"; text: deepseekAccount.statusText; Layout.fillWidth: true }
        ReadText { objectName: "deepseekAccountError"; text: deepseekAccount.error; visible: text.length > 0; Layout.fillWidth: true; color: appearance.colors.danger }
        Flow {
            Layout.fillWidth: true
            spacing: 8
            KButton { objectName: "deepseekAccountLogin"; text: qsTr("Accedi con DeepSeek"); primary: true; visible: !deepseekAccount.connected && !deepseekAccount.pending && !deepseekAccount.proxyEnabled; enabled: workspace.connected && !deepseekAccount.busy; onClicked: deepseekAccount.login() }
            KButton { objectName: "deepseekAccountCancel"; text: qsTr("Annulla accesso"); visible: deepseekAccount.pending; enabled: !deepseekAccount.busy; onClicked: deepseekAccount.cancel() }
            KButton { objectName: "deepseekAccountLogout"; text: qsTr("Disconnetti da Kira"); visible: deepseekAccount.connected && !deepseekAccount.proxyEnabled; enabled: !deepseekAccount.busy; onClicked: deepseekAccount.logout() }
            KButton { objectName: "deepseekAccountRefresh"; text: qsTr("Aggiorna stato"); enabled: workspace.connected && !deepseekAccount.busy; onClicked: deepseekAccount.refresh() }
        }

    }
}
