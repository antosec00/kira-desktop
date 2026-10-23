import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: pane
    property string objectPrefix: "chatGPTAccount"
    Layout.fillWidth: true
    implicitHeight: content.implicitHeight + 32
    radius: 12
    color: appearance.colors.surfaceAlt
    border.color: appearance.colors.border

    component Note: Text {
        Layout.fillWidth: true
        textFormat: Text.PlainText
        font: appearance.uiFont
        color: appearance.colors.muted
        wrapMode: Text.Wrap
    }

    ColumnLayout {
        id: content
        anchors.fill: parent
        anchors.margins: 16
        spacing: 12
        Note {
            text: qsTr("OpenAI diretto · ChatGPT")
            color: appearance.colors.text
            font: Qt.font({family: appearance.uiFont.family, pixelSize: appearance.uiFont.pixelSize,
                           weight: Font.DemiBold, italic: appearance.uiFont.italic})
        }
        Note {
            text: qsTr("Collega il tuo piano ChatGPT con l'accesso ufficiale. L'utilizzo condivide i limiti del piano; Kira non passa automaticamente a un'API a pagamento.")
        }
        Note {
            objectName: pane.objectPrefix + "ActiveLabel"
            text: qsTr("Account per le nuove chat: %1").arg(directAccount.activeLabel)
            color: appearance.colors.text
        }
        KComboBox {
            objectName: pane.objectPrefix + "Choice"
            Layout.fillWidth: true
            model: directAccount.accounts
            textRole: "label"
            valueRole: "id"
            currentIndex: directAccount.accounts.findIndex(function(account) { return account.id === directAccount.activeAccountId })
            displayText: currentIndex < 0 ? qsTr("Scegli un account collegato") : currentText
            visible: count > 0
            enabled: directAccount.editable
            onActivated: directAccount.select(String(currentValue))
            Accessible.name: qsTr("Account ChatGPT per le nuove chat")
        }
        Note {
            visible: directAccount.accounts.length > 0
            text: qsTr("Le chat esistenti mantengono il proprio account. Per usare un altro account, avvia una nuova chat.")
        }
        Note {
            objectName: pane.objectPrefix + "Status"
            text: directAccount.statusText
        }
        Flow {
            Layout.fillWidth: true
            spacing: 8
            KButton {
                objectName: pane.objectPrefix + "Login"
                text: qsTr("Continua con ChatGPT")
                primary: true
                enabled: directAccount.editable
                onClicked: directAccount.login(directAccount.activeAccountId)
            }
            KButton {
                objectName: pane.objectPrefix + "Add"
                text: qsTr("Aggiungi account")
                visible: directAccount.accounts.length > 0
                enabled: directAccount.editable
                onClicked: directAccount.login("")
            }
            KButton {
                objectName: pane.objectPrefix + "Cancel"
                text: qsTr("Annulla accesso")
                visible: directAccount.pending
                enabled: directAccount.canCancel
                onClicked: directAccount.cancel()
            }
            KButton {
                objectName: pane.objectPrefix + "Logout"
                text: qsTr("Disconnetti account")
                visible: directAccount.activeAccountId.length > 0
                enabled: directAccount.editable
                onClicked: directAccount.logout(directAccount.activeAccountId)
            }
            KButton {
                objectName: pane.objectPrefix + "Refresh"
                text: qsTr("Aggiorna stato")
                enabled: workspace.connected && !directAccount.busy
                onClicked: directAccount.refresh()
            }
            KButton {
                objectName: pane.objectPrefix + "Usage"
                text: qsTr("Utilizzo del piano ↗")
                enabled: !directAccount.usageOpening
                onClicked: directAccount.openUsage()
            }
            KButton {
                objectName: pane.objectPrefix + "CopyUsageLink"
                text: qsTr("Copia collegamento")
                onClicked: directAccount.copyUsageLink()
            }
        }
        Note {
            objectName: pane.objectPrefix + "UsageMessage"
            text: directAccount.usageMessage
            visible: text.length > 0
        }
        Note {
            objectName: pane.objectPrefix + "Warning"
            text: directAccount.warning
            visible: text.length > 0
            color: appearance.colors.warning
        }
        Note {
            objectName: pane.objectPrefix + "Error"
            text: directAccount.error
            visible: text.length > 0
            color: appearance.colors.danger
        }
    }
}
