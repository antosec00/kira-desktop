import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: card
    objectName: "accountUsageCard"
    readonly property var report: { localization.revision; return accountUsage.report }
    readonly property var providers: ["codex", "openai_chatgpt", "ollama", "claude_code"]
    readonly property bool chatgptLinks: accountUsage.backend === "codex" || accountUsage.backend === "openai_chatgpt"
    readonly property bool claudeLinks: accountUsage.backend === "claude_code"
    implicitHeight: column.implicitHeight + 28
    radius: 12
    color: appearance.colors.surfaceAlt
    border.color: appearance.colors.border
    ColumnLayout {
        id: column
        anchors.left: parent.left; anchors.right: parent.right
        anchors.top: parent.top; anchors.margins: 14
        spacing: 12
        ReadText {
            Layout.fillWidth: true
            text: qsTr("Utilizzo dell'account")
            font.pixelSize: appearance.uiFont.pixelSize + 3; font.bold: true
            color: appearance.colors.accent
        }
        RowLayout {
            Layout.fillWidth: true
            KComboBox {
                objectName: "usageProviderChoice"
                Layout.fillWidth: true
                model: ["Codex", qsTr("OpenAI diretto · ChatGPT"), "Ollama", qsTr("Claude Code")]
                currentIndex: card.providers.indexOf(accountUsage.backend)
                Accessible.name: qsTr("Provider di cui mostrare l'utilizzo")
                onActivated: accountUsage.setBackend(card.providers[currentIndex])
            }
            KButton {
                objectName: "accountUsageRefresh"
                text: accountUsage.busy ? qsTr("Aggiornamento…") : qsTr("Aggiorna")
                enabled: accountUsage.connected && !accountUsage.busy
                onClicked: accountUsage.refresh()
            }
        }
        ReadText {
            Layout.fillWidth: true
            text: qsTr("Questa scelta non cambia il provider della chat.")
            color: appearance.colors.muted
            font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1)
        }
        ReadText {
            objectName: "usageAccountIdentity"
            Layout.fillWidth: true
            visible: card.report.loaded
            text: card.report.source + " · " + card.report.account + "\n" + qsTr("Piano: %1").arg(card.report.plan)
            font.bold: true
        }
        ReadText {
            Layout.fillWidth: true
            visible: !!card.report.account_status
            text: card.report.account_status
            color: appearance.colors.warning
        }
        ReadText {
            Layout.fillWidth: true
            text: card.report.note
            color: appearance.colors.muted
        }
        ReadText {
            Layout.fillWidth: true
            visible: card.chatgptLinks || card.claudeLinks
            text: qsTr("Nel browser, verifica di usare lo stesso account mostrato in Kira.")
            color: appearance.colors.muted
        }
        ReadText {
            objectName: "usageBrowserMessage"
            Layout.fillWidth: true
            visible: (card.chatgptLinks && !!directAccount.usageMessage) || (card.claudeLinks && !!accountUsage.linkMessage)
            text: card.claudeLinks ? accountUsage.linkMessage : directAccount.usageMessage
            color: appearance.colors.muted
        }
        RowLayout {
            visible: card.chatgptLinks
            Layout.fillWidth: true
            KButton { objectName: "usageManageChatGPT"; text: qsTr("Apri ChatGPT ↗"); enabled: !directAccount.usageOpening; onClicked: directAccount.openUsage() }
            KButton { objectName: "usageCopyChatGPTLink"; text: qsTr("Copia collegamento"); onClicked: directAccount.copyUsageLink() }
            Item { Layout.fillWidth: true }
        }
        RowLayout {
            visible: card.claudeLinks
            Layout.fillWidth: true
            KButton { objectName: "usageManageClaude"; text: qsTr("Apri claude.ai ↗"); enabled: !accountUsage.linkOpening; onClicked: accountUsage.openPlanPage() }
            KButton { objectName: "usageCopyClaudeLink"; text: qsTr("Copia collegamento"); onClicked: accountUsage.copyPlanLink() }
            Item { Layout.fillWidth: true }
        }
        ReadText {
            objectName: "accountUsageEmpty"
            Layout.fillWidth: true
            visible: !card.report.loaded && !accountUsage.busy && !accountUsage.error
            text: accountUsage.connected ? qsTr("Premi Aggiorna per leggere l'utilizzo dell'account selezionato.") : qsTr("Connettiti al servizio per aggiornare l'utilizzo.")
            color: appearance.colors.muted
        }
        ReadText {
            objectName: "accountUsageError"
            Layout.fillWidth: true
            visible: !!accountUsage.error
            text: accountUsage.error
            color: appearance.colors.danger
        }
        ReadText {
            objectName: "accountUsageStale"
            Layout.fillWidth: true
            visible: card.report.loaded && !!accountUsage.error
            text: qsTr("Aggiornamento non riuscito. Questi sono gli ultimi dati ricevuti; verifica la data.")
            color: appearance.colors.muted
        }
        Repeater {
            model: card.report.groups
            delegate: ColumnLayout {
                required property var modelData
                required property int index
                Layout.fillWidth: true
                spacing: 9
                ReadText { Layout.fillWidth: true; text: modelData.name; font.bold: true }
                ReadText { Layout.fillWidth: true; visible: !!modelData.status; text: modelData.status; color: appearance.colors.warning }
                Repeater {
                    model: modelData.windows
                    delegate: ColumnLayout {
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true
                        spacing: 5
                        RowLayout {
                            Layout.fillWidth: true
                            ReadText { Layout.fillWidth: true; text: modelData.title }
                            ReadText { text: modelData.value; font.bold: true }
                        }
                        ProgressBar {
                            id: remainingBar
                            objectName: "accountUsageRemaining_" + index
                            Layout.fillWidth: true
                            from: 0; to: 100; value: modelData.remaining
                            visible: modelData.known
                            Accessible.name: modelData.title + " · " + modelData.value
                            background: Rectangle { implicitHeight: 7; radius: 3; color: appearance.colors.border }
                            contentItem: Item {
                                implicitHeight: 7
                                Rectangle { width: parent.width * remainingBar.visualPosition; height: parent.height; radius: 3; color: appearance.colors.accent }
                            }
                        }
                        ReadText { Layout.fillWidth: true; text: modelData.reset; color: appearance.colors.muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1) }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    ReadText { Layout.fillWidth: true; text: modelData.credits_label }
                    ReadText { text: modelData.credits; font.bold: true }
                }
            }
        }
        ReadText { Layout.fillWidth: true; visible: !!card.report.omission; text: card.report.omission; color: appearance.colors.muted }
        ReadText {
            objectName: "accountUsageObserved"
            Layout.fillWidth: true
            visible: card.report.loaded
            text: card.report.observed
            color: appearance.colors.muted
            font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1)
        }
        ColumnLayout {
            Layout.fillWidth: true
            visible: card.report.local_rows.length > 0
            spacing: 8
            ReadText { Layout.fillWidth: true; text: qsTr("Registrato da Kira"); font.bold: true }
            ReadText { Layout.fillWidth: true; text: card.report.local_note; color: appearance.colors.muted }
            ReadText { Layout.fillWidth: true; text: card.report.local_period; color: appearance.colors.muted }
            Repeater {
                model: card.report.local_rows
                delegate: RowLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    ReadText { Layout.fillWidth: true; text: modelData.label }
                    ReadText { text: modelData.value; font.bold: true }
                }
            }
        }
    }
}
