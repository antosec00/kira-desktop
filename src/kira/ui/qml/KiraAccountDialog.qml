import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The Kira Cloud account, reachable from the sidebar and the top bar.
Popup {
    id: kiraAccountDialog
    objectName: "kiraAccountDialog"
    modal: true
    focus: true
    padding: 0
    width: Math.min(parent ? parent.width - 48 : 680, 650)
    x: parent ? Math.round((parent.width - width) / 2) : 0
    y: parent ? Math.round((parent.height - height) / 2) : 0
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    signal openKiraCloudModels()

    onOpened: {
        kiraCloudAccount.clearMessages()
        kiraCloudAccount.refresh()
    }

    Overlay.modal: Rectangle {
        color: Qt.rgba(0, 0, 0, 0.65)
    }

    background: Rectangle {
        radius: 16
        color: appearance.colors.surfaceAlt
        border.width: 1
        border.color: appearance.colors.border
    }

    contentItem: ColumnLayout {
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: 16
            Layout.leftMargin: 20
            Layout.rightMargin: 20
            spacing: 10

            Rectangle {
                width: 10
                height: 10
                radius: 5
                color: kiraCloudAccount.connected ? appearance.colors.success : appearance.colors.muted
            }

            ReadText {
                Layout.fillWidth: true
                text: kiraCloudAccount.connected ? qsTr("Account Kira Cloud collegato") : qsTr("Accedi a Kira Cloud")
                color: appearance.colors.text
                font.bold: true
            }

            KButton {
                objectName: "kiraDialogCloseButton"
                subtle: true
                text: qsTr("Chiudi")
                onClicked: kiraAccountDialog.close()
            }
        }

        KiraCloudPane {
            objectName: "kiraDialogPane"
            compact: true
            Layout.leftMargin: 14
            Layout.rightMargin: 14
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 20
            Layout.rightMargin: 20
            Layout.bottomMargin: 16
            visible: kiraCloudAccount.connected
            spacing: 8

            KButton {
                objectName: "kiraDialogUseProviderButton"
                primary: true
                text: qsTr("Usa Kira Cloud in questa chat")
                onClicked: {
                    kiraAccountDialog.openKiraCloudModels()
                    kiraAccountDialog.close()
                }
            }

            Item { Layout.fillWidth: true }
        }

        Item {
            visible: !kiraCloudAccount.connected
            Layout.preferredHeight: 4
        }
    }
}
