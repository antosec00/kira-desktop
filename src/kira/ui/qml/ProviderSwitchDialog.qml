import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "providerSwitchDialog"
    title: qsTr("Continua la chat con un altro provider")
    modal: true
    anchors.centerIn: parent
    width: Math.min(620, parent.width - 40)
    height: Math.min(540, parent.height - 50)
    closePolicy: providerSwitch.applying ? Popup.NoAutoClose : Popup.CloseOnEscape
    font: appearance.uiFont
    onRejected: providerSwitch.cancel()
    signal configureRequested(string backend)
    readonly property bool needsSetup: !providerSwitch.loading && providerSwitch.models.length === 0 && providerSwitch.error.length > 0
    KFormat { id: kformat }
    background: Rectangle { color: appearance.colors.surface; radius: 14; border.color: appearance.colors.border }
    Connections {
        target: providerSwitch
        function onOpened() { dialog.open() }
        function onCompleted() { dialog.close() }
        function onDismissed() { dialog.close() }
    }
    contentItem: ScrollView {
        clip: true
        contentWidth: availableWidth
        ColumnLayout {
            width: parent.width
            spacing: 12
            Text {
                text: qsTr("La chat, il titolo e la bozza restano qui. Il nuovo provider riceverà il contesto portabile disponibile con il prossimo messaggio.")
                Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText
                font: appearance.uiFont; color: appearance.colors.text
            }
            Text {
                objectName: "switchProviderName"
                text: kformat.providerLabel(providerSwitch.backend)
                Layout.fillWidth: true; textFormat: Text.PlainText; font: appearance.uiFont; color: appearance.colors.accent
            }
            Text { text: qsTr("Account per questa chat"); visible: providerSwitch.backend === "openai_chatgpt"; font: appearance.uiFont; color: appearance.colors.text; textFormat: Text.PlainText }
            KComboBox {
                objectName: "switchProviderAccount"
                visible: providerSwitch.backend === "openai_chatgpt"
                Layout.fillWidth: true
                model: directAccount.accounts; textRole: "label"; valueRole: "id"
                currentIndex: indexOfValue(providerSwitch.accountId)
                displayText: currentIndex < 0 ? (providerSwitch.accountId ? qsTr("Account salvato non collegato") : qsTr("Scegli un account…")) : currentText
                enabled: !providerSwitch.applying
                onActivated: providerSwitch.setAccount(currentValue)
                Accessible.name: qsTr("Account per questa chat")
            }
            DeepSeekAccountPane {
                visible: providerSwitch.backend === "deepseek_account"
                Layout.fillWidth: true
            }
            GoogleCloudPane {
                visible: providerSwitch.backend === "google_cloud"
                Layout.fillWidth: true
            }
            Text { text: qsTr("Modello"); font: appearance.uiFont; color: appearance.colors.text; textFormat: Text.PlainText }
            KComboBox {
                objectName: "switchProviderModel"
                Layout.fillWidth: true
                model: providerSwitch.models; textRole: "name"; valueRole: "id"
                currentIndex: indexOfValue(providerSwitch.model)
                displayText: currentIndex < 0 ? (providerSwitch.model || qsTr("Scegli un modello…")) : currentText
                enabled: !providerSwitch.loading && !providerSwitch.applying
                onActivated: providerSwitch.setModel(currentValue)
                Accessible.name: qsTr("Modello")
            }
            Text { text: qsTr("Livello di ragionamento"); visible: kformat.offersEffort(providerSwitch.backend); font: appearance.uiFont; color: appearance.colors.text; textFormat: Text.PlainText }
            KComboBox {
                objectName: "switchProviderEffort"
                visible: kformat.offersEffort(providerSwitch.backend)
                Layout.fillWidth: true
                model: [qsTr("Predefinito del provider")].concat(providerSwitch.efforts)
                currentIndex: Math.max(0, providerSwitch.efforts.indexOf(providerSwitch.effort) + 1)
                displayText: currentIndex === 0 && providerSwitch.effort.length ? qsTr("%1 · salvato").arg(providerSwitch.effort) : currentText
                enabled: !providerSwitch.loading && !providerSwitch.applying
                onActivated: providerSwitch.setEffort(currentIndex === 0 ? "" : currentText)
                Accessible.name: qsTr("Livello di ragionamento")
            }
            Text {
                visible: providerSwitch.loading || providerSwitch.error.length > 0
                text: providerSwitch.loading ? qsTr("Caricamento dei modelli…") : providerSwitch.error
                Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText
                font: appearance.uiFont; color: appearance.colors.warning
            }
            Flow {
                Layout.fillWidth: true; spacing: 8
                KButton {
                    objectName: "switchProviderConfigure"
                    visible: dialog.needsSetup
                    text: qsTr("Configura %1").arg(kformat.providerLabel(providerSwitch.backend)); primary: true
                    onClicked: { const backend = providerSwitch.backend; providerSwitch.cancel(); dialog.close(); dialog.configureRequested(backend) }
                }
                KButton { text: qsTr("Aggiorna modelli"); enabled: !providerSwitch.loading && !providerSwitch.applying; onClicked: providerSwitch.refreshCatalog() }
            }
        }
    }
    footer: RowLayout {
        spacing: 8
        Item { Layout.fillWidth: true }
        KButton { objectName: "switchProviderCancel"; text: qsTr("Annulla"); enabled: !providerSwitch.applying; onClicked: { providerSwitch.cancel(); dialog.close() } }
        KButton { objectName: "switchProviderApply"; text: providerSwitch.applying ? qsTr("Cambio in corso…") : qsTr("Continua in questa chat"); enabled: providerSwitch.canApply; primary: true; onClicked: providerSwitch.apply() }
    }
}
