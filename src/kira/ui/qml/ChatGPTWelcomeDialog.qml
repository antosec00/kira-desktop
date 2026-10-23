import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "chatGPTWelcomeDialog"
    modal: true
    anchors.centerIn: parent
    width: Math.min(600, parent.width - 48)
    height: Math.min(370 + Math.max(0, appearance.uiFont.pixelSize - 13) * 10, parent.height - 48)
    visible: directAccount.welcomePending
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    onClosed: if (directAccount.welcomePending) directAccount.dismissWelcome()
    background: Rectangle { radius: 14; color: appearance.colors.surface; border.color: appearance.colors.border }
    ColumnLayout {
        anchors.fill: parent
        spacing: 16
        ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                width: scroll.availableWidth
                spacing: 16
                Text {
                    Layout.fillWidth: true
                    text: qsTr("Stai usando il tuo piano ChatGPT")
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    font: Qt.font({family: appearance.uiFont.family, pixelSize: appearance.uiFont.pixelSize + 8, weight: Font.DemiBold})
                    color: appearance.colors.text
                }
                ReadText {
                    Layout.fillWidth: true
                    text: qsTr("Il piano è collegato a Kira. Le richieste con OpenAI diretto condividono i limiti di utilizzo del piano ChatGPT, inclusi quelli condivisi con Codex e ChatGPT Work.")
                }
                ReadText {
                    Layout.fillWidth: true
                    text: qsTr("Puoi controllare l'utilizzo e gestire quanto piano condividere con Kira nelle impostazioni di ChatGPT. Il collegamento non importa le conversazioni o i ricordi di ChatGPT.")
                    color: appearance.colors.muted
                }
            }
        }
        ReadText {
            objectName: "chatGPTWelcomeUsageMessage"
            Layout.fillWidth: true
            visible: !!directAccount.usageMessage
            text: directAccount.usageMessage
            color: appearance.colors.muted
        }
        Flow {
            Layout.fillWidth: true
            spacing: 10
            KButton {
                objectName: "chatGPTWelcomeUsage"
                text: qsTr("Utilizzo del piano ↗")
                enabled: !directAccount.usageOpening
                onClicked: directAccount.openUsage()
            }
            KButton {
                objectName: "chatGPTWelcomeCopyUsageLink"
                text: qsTr("Copia collegamento")
                onClicked: directAccount.copyUsageLink()
            }
            KButton {
                objectName: "chatGPTWelcomeDismiss"
                text: qsTr("Ho capito")
                primary: true
                onClicked: directAccount.dismissWelcome()
            }
        }
    }
}
