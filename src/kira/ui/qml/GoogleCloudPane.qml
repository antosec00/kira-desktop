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

    property string pendingMode: ""
    readonly property string selectedMode: pendingMode.length > 0 ? pendingMode : googleAccount.authMode
    onVisibleChanged: if (!visible) pendingMode = ""

    Connections {
        target: googleAccount
        function onChanged() {
            if (pane.pendingMode === googleAccount.authMode) pane.pendingMode = ""
        }
    }

    ColumnLayout {
        id: content
        anchors.fill: parent
        anchors.margins: 16
        spacing: 10

        ReadText {
            text: qsTr("Google Cloud · Vertex AI e Gemini")
            color: appearance.colors.text
            font.bold: true
            Layout.fillWidth: true
        }
        ReadText {
            text: qsTr("Usa i modelli Google Cloud (Gemini 2.5/3, Argon e Claude Opus/Sonnet su Vertex AI) con crediti Google Cloud o saldo a consumo tramite Application Default Credentials (gcloud ADC su WSL2/Linux) oppure con una chiave API.")
            Layout.fillWidth: true
            color: appearance.colors.muted
        }

        Flow {
            Layout.fillWidth: true
            spacing: 8
            KButton {
                objectName: "googleAuthModeAdc"
                text: qsTr("Google Cloud Login (gcloud / ADC)")
                checked: pane.selectedMode === "adc"
                enabled: workspace.connected && !googleAccount.busy
                onClicked: pane.pendingMode = "adc"
            }
            KButton {
                objectName: "googleAuthModeApiKey"
                text: qsTr("Chiave API Google / Gemini")
                checked: pane.selectedMode === "api_key"
                enabled: workspace.connected && !googleAccount.busy
                onClicked: pane.pendingMode = "api_key"
            }
        }

        ColumnLayout {
            visible: pane.selectedMode === "adc"
            Layout.fillWidth: true
            spacing: 6
            Label { text: qsTr("Account Google Cloud (isolati da WSL2)") }
            ReadText {
                text: qsTr("L'account di sistema WSL2 (gcloud) viene letto in sola lettura senza mai disconnettere il terminale o Antigravity. Puoi aggiungere altri account Google isolati solo per Kira e passare da uno all'altro con un clic.")
                Layout.fillWidth: true
                color: appearance.colors.muted
            }
            Flow {
                Layout.fillWidth: true
                spacing: 8
                visible: googleAccount.accounts.length > 0
                Repeater {
                    model: googleAccount.accounts
                    delegate: KButton {
                        required property var modelData
                        objectName: "googleAccountOption_" + modelData.id
                        text: modelData.label
                        checked: googleAccount.activeAccountId === modelData.id
                        enabled: workspace.connected && !googleAccount.busy
                        onClicked: googleAccount.selectAccount(modelData.id)
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6
            Label { text: qsTr("Google Cloud Project ID") }
            TextField {
                id: projectInput
                objectName: "googleProjectId"
                text: googleAccount.configuredProjectId
                placeholderText: googleAccount.detectedProjectId.length > 0
                    ? qsTr("Rilevato automaticamente da gcloud: %1").arg(googleAccount.detectedProjectId)
                    : qsTr("es. my-gcp-project-123 (richiesto per Vertex AI)")
                selectByMouse: true
                Layout.fillWidth: true
            }
            Label { text: qsTr("Regione Vertex AI (Location)") }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                TextField {
                    id: locationInput
                    objectName: "googleLocation"
                    text: googleAccount.location
                    placeholderText: "global"
                    selectByMouse: true
                    Layout.fillWidth: true
                }
            }
            Flow {
                Layout.fillWidth: true
                spacing: 6
                Repeater {
                    model: googleAccount.supportedLocations
                    delegate: KButton {
                        required property string modelData
                        text: modelData
                        subtle: true; quietSelection: true
                        checked: locationInput.text === modelData
                        enabled: workspace.connected && !googleAccount.busy
                        onClicked: locationInput.text = modelData
                    }
                }
            }
        }

        ColumnLayout {
            visible: pane.selectedMode === "api_key"
            Layout.fillWidth: true
            spacing: 6
            Label { text: qsTr("Chiave API Google Cloud / Gemini") }
            TextField {
                id: apiKeyInput
                objectName: "googleApiKey"
                echoMode: TextInput.Password
                placeholderText: googleAccount.apiKeyConfigured
                    ? qsTr("Configurata; lascia vuoto per mantenerla")
                    : qsTr("Inserisci la chiave API (AIza... o Vertex API key)")
                selectByMouse: true
                Layout.fillWidth: true
            }
            ReadText {
                text: qsTr("La chiave viene conservata nel profilo privato Kira con permessi ristretti e non viene mai mostrata in chiaro.")
                Layout.fillWidth: true
                color: appearance.colors.muted
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6
            Label { text: qsTr("Modelli Vertex AI aggiuntivi (opzionale)") }
            TextField {
                id: customModelsInput
                objectName: "googleCustomModels"
                text: googleAccount.customModelsText
                placeholderText: qsTr("ID separati da virgola, es. gemini-2.5-pro-preview, claude-opus-5-5")
                selectByMouse: true
                Layout.fillWidth: true
            }
        }

        Flow {
            Layout.fillWidth: true
            spacing: 8
            KButton {
                objectName: "googleConfigSave"
                text: qsTr("Salva configurazione Google Cloud")
                primary: true
                enabled: workspace.connected && !googleAccount.busy
                onClicked: {
                    googleAccount.configure(
                        pane.selectedMode,
                        projectInput.text,
                        locationInput.text.length > 0 ? locationInput.text : "global",
                        apiKeyInput.text,
                        customModelsInput.text
                    )
                    apiKeyInput.clear()
                }
            }
            KButton {
                objectName: "googleAdcLogin"
                text: qsTr("Aggiungi / Cambia account Google (isolato in Kira)")
                visible: pane.selectedMode === "adc" && !googleAccount.pending
                enabled: workspace.connected && !googleAccount.busy
                onClicked: googleAccount.login()
            }
            KButton {
                objectName: "googleAdcCancel"
                text: qsTr("Annulla accesso")
                visible: googleAccount.pending
                enabled: !googleAccount.busy
                onClicked: googleAccount.cancel()
            }
            KButton {
                objectName: "googleAccountLogout"
                text: pane.selectedMode === "api_key"
                    ? qsTr("Rimuovi chiave API")
                    : qsTr("Rimuovi account isolato da Kira")
                visible: (pane.selectedMode === "api_key" && googleAccount.apiKeyConfigured)
                    || (pane.selectedMode === "adc" && googleAccount.connected && !googleAccount.activeAccountIsSystem)
                enabled: workspace.connected && !googleAccount.busy
                onClicked: googleAccount.logout(pane.selectedMode === "adc" ? googleAccount.activeAccountId : "")
            }
            KButton {
                objectName: "googleAccountRefresh"
                text: qsTr("Aggiorna stato")
                enabled: workspace.connected && !googleAccount.busy
                onClicked: googleAccount.refresh()
            }
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 1
            color: appearance.colors.border
        }
        ReadText {
            objectName: "googleAccountStatus"
            text: googleAccount.statusText
            Layout.fillWidth: true
        }
        ReadText {
            objectName: "googleAccountError"
            text: googleAccount.error
            visible: text.length > 0
            Layout.fillWidth: true
            color: appearance.colors.danger
        }
    }
}
