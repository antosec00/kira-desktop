import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Kira Cloud account: browser sign-in when disconnected, plan and credit when connected.
Rectangle {
    id: root
    property bool compact: false
    property bool showTokenLogin: false
    Layout.fillWidth: true
    implicitHeight: body.implicitHeight + 32
    radius: 12
    color: appearance.colors.surfaceAlt
    border.width: 1
    border.color: appearance.colors.border

    component CreditCard: Rectangle {
        id: card
        property string title: ""
        property string amountText: ""
        property string metaText: ""
        property real remainingPct: 0
        property color barColor: appearance.colors.accent
        Layout.fillWidth: true
        implicitHeight: cardColumn.implicitHeight + 22
        radius: 10
        color: appearance.colors.surface
        border.width: 1
        border.color: appearance.colors.border

        ColumnLayout {
            id: cardColumn
            anchors.fill: parent
            anchors.margins: 12
            spacing: 6

            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    text: card.title
                    color: appearance.colors.text
                    font.family: appearance.uiFont.family
                    font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 2)
                    font.bold: true
                    elide: Text.ElideRight
                }
                Text {
                    text: Math.round(card.remainingPct) + "%"
                    color: appearance.colors.muted
                    font.family: appearance.codeFont.family
                    font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 1)
                    font.bold: true
                }
            }

            ReadText {
                Layout.fillWidth: true
                text: card.amountText
                color: appearance.colors.text
                font.bold: true
            }

            Rectangle {
                Layout.fillWidth: true
                height: 8
                radius: 4
                color: appearance.colors.surfaceAlt
                border.width: 1
                border.color: appearance.colors.border

                Rectangle {
                    width: Math.max(0, Math.min(parent.width, parent.width * (card.remainingPct / 100.0)))
                    height: parent.height
                    radius: 4
                    color: card.barColor
                }
            }

            ReadText {
                Layout.fillWidth: true
                visible: card.metaText.length > 0
                text: card.metaText
                color: appearance.colors.muted
            }
        }
    }

    ColumnLayout {
        id: body
        anchors.fill: parent
        anchors.margins: 16
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 3

                ReadText {
                    Layout.fillWidth: true
                    text: qsTr("Kira Cloud · Account e credito AI")
                    color: appearance.colors.text
                    font.bold: true
                }

                ReadText {
                    Layout.fillWidth: true
                    text: qsTr("I modelli AI inclusi nel tuo piano Kira, pronti all'uso con tutti gli strumenti di Kira Desktop. Nessuna chiave da configurare.")
                    color: appearance.colors.muted
                }
            }

            Rectangle {
                visible: kiraCloudAccount.connected
                radius: 8
                color: kiraCloudAccount.totalAvailable > 0 ? Qt.rgba(0.18, 0.72, 0.45, 0.16) : Qt.rgba(0.92, 0.62, 0.18, 0.18)
                border.width: 1
                border.color: kiraCloudAccount.totalAvailable > 0 ? Qt.rgba(0.18, 0.72, 0.45, 0.45) : Qt.rgba(0.92, 0.62, 0.18, 0.45)
                implicitWidth: badgeLabel.implicitWidth + 18
                implicitHeight: badgeLabel.implicitHeight + 10

                Text {
                    id: badgeLabel
                    objectName: "kiraCloudPlanBadge"
                    anchors.centerIn: parent
                    text: kiraCloudAccount.planName
                    color: appearance.colors.text
                    font.family: appearance.uiFont.family
                    font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 2)
                    font.bold: true
                }
            }
        }

        // ---------------------------------------------------------------- signed out
        ColumnLayout {
            Layout.fillWidth: true
            visible: !kiraCloudAccount.connected
            spacing: 10

            ReadText {
                Layout.fillWidth: true
                visible: kiraCloudAccount.sessionExpired
                text: qsTr("La sessione Kira Cloud non è più valida. Accedi di nuovo per continuare.")
                color: appearance.colors.warning
            }

            ReadText {
                Layout.fillWidth: true
                text: kiraCloudAccount.browserLoginPending
                    ? qsTr("Completa l'accesso nella pagina appena aperta nel browser: Kira Desktop si collegherà da solo.")
                    : qsTr("L'accesso avviene sul sito di Kira, nel tuo browser: qui non devi inserire alcuna password.")
                color: kiraCloudAccount.browserLoginPending ? appearance.colors.accent : appearance.colors.muted
            }

            Flow {
                Layout.fillWidth: true
                spacing: 8

                KButton {
                    objectName: "kiraCloudBrowserLoginBtn"
                    primary: true
                    enabled: !kiraCloudAccount.busy && !kiraCloudAccount.browserLoginPending
                    text: kiraCloudAccount.browserLoginPending ? qsTr("In attesa del browser…") : qsTr("Accedi o registrati nel browser")
                    onClicked: kiraCloudAccount.startBrowserLogin()
                }

                KButton {
                    objectName: "kiraCloudCancelLoginBtn"
                    visible: kiraCloudAccount.browserLoginPending
                    subtle: true
                    text: qsTr("Annulla")
                    onClicked: kiraCloudAccount.cancelBrowserLogin()
                }

                KButton {
                    objectName: "kiraCloudTokenToggleBtn"
                    subtle: true
                    text: root.showTokenLogin ? qsTr("Nascondi accesso con token") : qsTr("Accedi con un token")
                    onClicked: root.showTokenLogin = !root.showTokenLogin
                }
            }

            RowLayout {
                Layout.fillWidth: true
                visible: root.showTokenLogin
                spacing: 8

                TextField {
                    id: tokenField
                    objectName: "kiraCloudTokenField"
                    Layout.fillWidth: true
                    selectByMouse: true
                    echoMode: TextInput.Password
                    placeholderText: qsTr("Token Kira Desktop dalla Console (kira_usr_…)")
                }

                KButton {
                    objectName: "kiraCloudTokenConnectBtn"
                    text: qsTr("Collega")
                    enabled: !kiraCloudAccount.busy && tokenField.text.trim().length > 0
                    onClicked: {
                        kiraCloudAccount.connectWithToken(tokenField.text)
                        tokenField.clear()
                    }
                }
            }
        }

        // ---------------------------------------------------------------- signed in
        ColumnLayout {
            Layout.fillWidth: true
            visible: kiraCloudAccount.connected
            spacing: 10

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: summaryRow.implicitHeight + 20
                radius: 10
                color: appearance.colors.surface
                border.width: 1
                border.color: appearance.colors.border

                RowLayout {
                    id: summaryRow
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 12

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3

                        ReadText {
                            Layout.fillWidth: true
                            text: kiraCloudAccount.userName.length > 0
                                ? (kiraCloudAccount.userName + " · " + kiraCloudAccount.userEmail)
                                : kiraCloudAccount.userEmail
                            color: appearance.colors.text
                            font.bold: true
                        }

                        ReadText {
                            Layout.fillWidth: true
                            visible: kiraCloudAccount.limitsText.length > 0
                            text: kiraCloudAccount.limitsText
                            color: appearance.colors.muted
                        }
                    }

                    ColumnLayout {
                        spacing: 2
                        Layout.alignment: Qt.AlignRight

                        Text {
                            Layout.alignment: Qt.AlignRight
                            text: qsTr("CREDITO DISPONIBILE")
                            color: appearance.colors.muted
                            font.family: appearance.uiFont.family
                            font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 3)
                            font.bold: true
                        }

                        Text {
                            objectName: "kiraCloudTotalAvailable"
                            Layout.alignment: Qt.AlignRight
                            text: kiraCloudAccount.totalAvailableText
                            color: kiraCloudAccount.totalAvailable > 0 ? appearance.colors.text : appearance.colors.danger
                            font.family: appearance.codeFont.family
                            font.pixelSize: appearance.uiFont.pixelSize + 3
                            font.bold: true
                        }
                    }
                }
            }

            GridLayout {
                Layout.fillWidth: true
                columns: root.compact || !kiraCloudAccount.hasExtraCredits ? 1 : 2
                rowSpacing: 10
                columnSpacing: 10

                CreditCard {
                    title: qsTr("CREDITO MENSILE DEL PIANO")
                    amountText: kiraCloudAccount.monthlyText
                    metaText: kiraCloudAccount.monthlyMetaText
                    remainingPct: kiraCloudAccount.monthlyRemainingPct
                }

                CreditCard {
                    visible: kiraCloudAccount.hasExtraCredits
                    title: qsTr("CREDITI EXTRA")
                    amountText: kiraCloudAccount.creditsText
                    metaText: qsTr("Si usano quando il credito mensile è esaurito. Non scadono.")
                    remainingPct: kiraCloudAccount.creditsRemainingPct
                    barColor: Qt.rgba(0.20, 0.78, 0.55, 0.90)
                }
            }

            Rectangle {
                Layout.fillWidth: true
                visible: kiraCloudAccount.totalAvailable <= 0.0
                implicitHeight: zeroWarnText.implicitHeight + 18
                radius: 8
                color: Qt.rgba(0.92, 0.62, 0.18, 0.14)
                border.width: 1
                border.color: Qt.rgba(0.92, 0.62, 0.18, 0.45)

                ReadText {
                    id: zeroWarnText
                    anchors.fill: parent
                    anchors.margins: 9
                    text: qsTr("Il tuo account è collegato ma non ha credito AI disponibile. Attiva o rinnova il piano dalla Console per usare i modelli Kira Cloud.")
                    color: appearance.colors.text
                }
            }

            Flow {
                Layout.fillWidth: true
                spacing: 8

                KButton {
                    objectName: "kiraCloudRefreshBtn"
                    primary: true
                    enabled: !kiraCloudAccount.busy
                    text: kiraCloudAccount.busy ? qsTr("Aggiornamento…") : qsTr("Aggiorna credito")
                    onClicked: {
                        kiraCloudAccount.refresh()
                        workspace.refreshModels()
                        accountUsage.refresh()
                    }
                }

                KButton {
                    objectName: "kiraCloudConsoleBtn"
                    enabled: !kiraCloudAccount.busy
                    text: qsTr("Gestisci il piano nella Console")
                    onClicked: kiraCloudAccount.openConsole()
                }

                KButton {
                    objectName: "kiraCloudLogoutBtn"
                    subtle: true
                    enabled: !kiraCloudAccount.busy
                    text: qsTr("Esci dall'account")
                    onClicked: kiraCloudAccount.logout()
                }
            }
        }

        ReadText {
            Layout.fillWidth: true
            visible: kiraCloudAccount.error.length > 0
            text: kiraCloudAccount.error
            color: appearance.colors.danger
        }
    }
}
