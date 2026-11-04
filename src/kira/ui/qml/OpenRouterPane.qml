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
            text: qsTr("OpenRouter · Accesso unificato a tutti i modelli")
            color: appearance.colors.text
            font.bold: true
            Layout.fillWidth: true
        }
        ReadText {
            text: qsTr("Collega la tua chiave API OpenRouter (sk-or-v1-...) per usare qualsiasi modello del catalogo OpenRouter (Claude, GPT, Gemini, DeepSeek, Qwen, Llama, Kimi, Mistral) con tutti gli strumenti nativi di Kira: file, terminale, Browser e Computer isolato, Team multi-agente, Knowledge Base e Kira Studio. Kira adatta automaticamente finestra di contesto, max_tokens, reasoning/thinking, prompt caching e visione multimodale in base alle capacità reali del modello scelto.")
            Layout.fillWidth: true
            color: appearance.colors.muted
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6
            Label { text: qsTr("Chiave API OpenRouter") }
            TextField {
                id: apiKeyInput
                objectName: "openrouterApiKey"
                echoMode: TextInput.Password
                placeholderText: openrouterAccount.apiKeyConfigured
                    ? qsTr("Configurata; lascia vuoto per mantenerla")
                    : openrouterAccount.usingEnvKey
                    ? qsTr("Attiva da OPENROUTER_API_KEY; inserisci per salvare nel profilo Kira")
                    : qsTr("Inserisci la chiave API OpenRouter (sk-or-v1-...)")
                selectByMouse: true
                Layout.fillWidth: true
            }
            ReadText {
                text: qsTr("La chiave viene salvata nel profilo locale privato di Kira con permessi 0600, oscurata nei log e mai esposta in chiaro.")
                Layout.fillWidth: true
                color: appearance.colors.muted
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6
            Label { text: qsTr("Modelli OpenRouter preferiti o personalizzati (opzionale)") }
            TextField {
                id: customModelsInput
                objectName: "openrouterCustomModels"
                text: openrouterAccount.customModelsText
                placeholderText: qsTr("ID separati da virgola, es. anthropic/claude-sonnet-4.6, deepseek/deepseek-r1, openai/gpt-5.4")
                selectByMouse: true
                Layout.fillWidth: true
            }
            ReadText {
                text: qsTr("Il catalogo completo viene caricato automaticamente da OpenRouter (/api/v1/models). Gli ID indicati qui vengono sempre messi in evidenza in cima alla lista.")
                Layout.fillWidth: true
                color: appearance.colors.muted
            }
        }

        ReadText {
            objectName: "openrouterAccountStatus"
            text: openrouterAccount.statusText
            Layout.fillWidth: true
        }
        ReadText {
            objectName: "openrouterAccountUsage"
            text: openrouterAccount.usageSummary
            visible: text.length > 0
            Layout.fillWidth: true
            color: appearance.colors.muted
        }
        ReadText {
            objectName: "openrouterAccountError"
            text: openrouterAccount.error
            visible: text.length > 0
            Layout.fillWidth: true
            color: appearance.colors.danger
        }

        Flow {
            Layout.fillWidth: true
            spacing: 8
            KButton {
                objectName: "openrouterConfigSave"
                text: qsTr("Salva configurazione OpenRouter")
                primary: true
                enabled: workspace.connected && !openrouterAccount.busy
                onClicked: {
                    openrouterAccount.configure(apiKeyInput.text, customModelsInput.text)
                    apiKeyInput.clear()
                }
            }
            KButton {
                objectName: "openrouterAccountRefresh"
                text: qsTr("Aggiorna catalogo e stato")
                enabled: workspace.connected && !openrouterAccount.busy
                onClicked: openrouterAccount.refresh()
            }
            KButton {
                objectName: "openrouterAccountLogout"
                text: qsTr("Rimuovi chiave salvata")
                visible: openrouterAccount.apiKeyConfigured
                enabled: workspace.connected && !openrouterAccount.busy
                onClicked: openrouterAccount.logout()
            }
        }
    }
}
