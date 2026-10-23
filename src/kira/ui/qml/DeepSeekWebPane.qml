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

    ColumnLayout {
        id: content
        anchors.fill: parent
        anchors.margins: 16
        spacing: 10

        ReadText {
            text: qsTr("DeepSeek Web · accesso diretto")
            color: appearance.colors.text
            font.bold: true
            Layout.fillWidth: true
        }
        ReadText {
            text: qsTr("Questo provider usa la sessione web di chat.deepseek.com e non passa dall’accesso DeepSeek Account, da PKCE o da una API key.")
            color: appearance.colors.muted
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        ReadText {
            text: qsTr("Kira può usare strumenti mediati dal servizio: browser e ricerca web, strumenti del progetto, file e comandi. DeepSeek V4 Flash e DeepSeek Reasoner possono analizzare immagini allegate e screenshot del Browser. Computer non è disponibile. Ogni chiamata rispetta modalità, permessi e approvazioni Kira.")
            color: appearance.colors.muted
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Label { text: qsTr("Token web DeepSeek (userToken)") }
        TextField {
            id: webToken
            objectName: "deepseekWebToken"
            echoMode: TextInput.Password
            placeholderText: deepseekAccount.webConnected
                ? qsTr("Token salvato; lascia vuoto per mantenerlo")
                : qsTr("Incolla il token da chat.deepseek.com")
            selectByMouse: true
            enabled: workspace.connected && !deepseekAccount.webBusy
            Layout.fillWidth: true
        }
        ReadText {
            text: qsTr("Per ottenerlo, apri chat.deepseek.com e cerca userToken in Application → Local Storage. Kira lo conserva nel profilo privato selezionato e lo invia solo a chat.deepseek.com.")
            color: appearance.colors.muted
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Flow {
            Layout.fillWidth: true
            spacing: 8
            KButton {
                objectName: "deepseekWebLogin"
                text: qsTr("Verifica e collega token")
                primary: true
                visible: !deepseekAccount.webConnected
                enabled: workspace.connected && !deepseekAccount.webBusy && webToken.text.length > 0
                onClicked: {
                    deepseekAccount.loginWeb(webToken.text)
                    webToken.clear()
                }
            }
            KButton {
                objectName: "deepseekWebLogout"
                text: qsTr("Rimuovi token web")
                visible: deepseekAccount.webConnected
                enabled: workspace.connected && !deepseekAccount.webBusy
                onClicked: deepseekAccount.logoutWeb()
            }
        }
        ReadText {
            objectName: "deepseekWebStatus"
            visible: deepseekAccount.webStatus.length > 0
            text: deepseekAccount.webStatus
            color: deepseekAccount.webError.length > 0 ? appearance.colors.danger : appearance.colors.muted
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        ReadText {
            objectName: "deepseekWebError"
            visible: deepseekAccount.webError.length > 0
            text: deepseekAccount.webError
            color: appearance.colors.danger
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        ColumnLayout {
            visible: deepseekAccount.webModels.length > 0
            Layout.fillWidth: true
            spacing: 4
            ReadText { text: qsTr("Modelli esposti dal provider DeepSeek Web"); color: appearance.colors.text; font.bold: true; Layout.fillWidth: true }
            Repeater {
                model: deepseekAccount.webModels
                delegate: ReadText {
                    required property var modelData
                    text: (modelData.name || modelData.id) + " · " + modelData.id
                    color: appearance.colors.muted
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                }
            }
        }
    }
}
