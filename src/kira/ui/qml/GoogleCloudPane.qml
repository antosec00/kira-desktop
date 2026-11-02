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
            text: qsTr("Usa i modelli Google Cloud con crediti Google Cloud o saldo a consumo tramite Application Default Credentials (gcloud ADC su WSL2/Linux) oppure con una chiave API. Con i crediti gratuiti della prova Google Cloud sono inclusi tutti i modelli nativi Google (Gemini 4 Argon, Gemini 3.1 Pro, Gemini 3.8/3.5 Flash); i modelli Partner (Claude Opus/Sonnet su Vertex AI) richiedono invece l'attivazione di un ciclo di fatturazione reale.")
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
                text: qsTr("L'account di sistema WSL2 (gcloud) viene letto in sola lettura senza mai disconnettere il terminale o Antigravity. Puoi aggiungere altri account Google isolati solo per Kira, passare da uno all'altro o escludere qualsiasi account dal pool di Kira con un clic.")
                Layout.fillWidth: true
                color: appearance.colors.muted
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 6
                visible: googleAccount.accounts.length > 0
                Repeater {
                    model: googleAccount.accounts
                    delegate: RowLayout {
                        required property var modelData
                        spacing: 6
                        KButton {
                            objectName: "googleAccountOption_" + modelData.id
                            text: modelData.label
                            checked: googleAccount.activeAccountId === modelData.id && !modelData.excluded
                            subtle: modelData.excluded
                            enabled: workspace.connected && !googleAccount.busy
                            onClicked: googleAccount.selectAccount(modelData.id)
                        }
                        KButton {
                            objectName: "googleAccountToggle_" + modelData.id
                            text: modelData.excluded ? qsTr("Riattiva in Kira") : qsTr("Escludi da Kira")
                            subtle: true
                            enabled: workspace.connected && !googleAccount.busy
                            onClicked: googleAccount.setAccountEnabled(modelData.id, modelData.excluded)
                        }
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6
            Label { text: qsTr("Continuità modello in caso di quota 429") }
            Flow {
                Layout.fillWidth: true
                spacing: 8
                KButton {
                    objectName: "googleFallbackModeAuto"
                    text: qsTr("Scala automaticamente a Gemini 3.1 Pro / Flash")
                    checked: !googleAccount.strictModel
                    enabled: workspace.connected && !googleAccount.busy
                    onClicked: googleAccount.setStrictModel(false)
                }
                KButton {
                    objectName: "googleFallbackModeStrict"
                    text: qsTr("Attendi sempre il modello selezionato (es. Gemini 4 Argon)")
                    checked: googleAccount.strictModel
                    enabled: workspace.connected && !googleAccount.busy
                    onClicked: googleAccount.setStrictModel(true)
                }
            }
            ReadText {
                text: googleAccount.strictModel
                    ? qsTr("Modalità attesa attiva: se tutti gli account del pool raggiungono il limite HTTP 429 o un timeout temporaneo, Kira attende il ripristino della quota e riprova a rotazione sugli account abilitati senza mai declassare il turno a modelli inferiori.")
                    : qsTr("Modalità fallback attiva: se tutti gli account del pool esauriscono temporaneamente la quota sul modello scelto, Kira può passare automaticamente a Gemini 3.1 Pro o Flash per proseguire subito.")
                Layout.fillWidth: true
                color: appearance.colors.muted
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
                objectName: "googleAccountExcludeActive"
                text: qsTr("Escludi account selezionato dal pool di Kira")
                visible: pane.selectedMode === "adc" && googleAccount.connected && googleAccount.activeAccountId.length > 0
                enabled: workspace.connected && !googleAccount.busy
                onClicked: googleAccount.setAccountEnabled(googleAccount.activeAccountId, false)
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
