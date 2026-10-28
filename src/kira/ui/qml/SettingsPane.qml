import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: settingsPage
    objectName: "settingsPage"
    signal diagnosticsRequested()
    signal soulsRequested()
    signal updateRequested()
    property int selectedSection: 0
    function showSection(index) {
        selectedSection = index
        pane.contentItem.contentY = 0
    }

    component Heading: Text {
        Layout.fillWidth: true
        Layout.topMargin: 16
        textFormat: Text.PlainText
        color: appearance.colors.text
        font: Qt.font({family: appearance.uiFont.family, pixelSize: appearance.uiFont.pixelSize + 6,
                       weight: Font.DemiBold, italic: appearance.uiFont.italic})
        wrapMode: Text.Wrap
    }
    component Note: Text {
        Layout.fillWidth: true
        textFormat: Text.PlainText
        color: appearance.colors.muted
        font: Qt.font({family: appearance.uiFont.family, pixelSize: Math.max(11, appearance.uiFont.pixelSize - 1),
                       weight: appearance.uiFont.weight, italic: appearance.uiFont.italic})
        wrapMode: Text.Wrap
        lineHeight: 1.25
    }
    component Rule: Rectangle {
        Layout.fillWidth: true
        Layout.topMargin: 10
        Layout.bottomMargin: 2
        implicitHeight: 1
        color: appearance.colors.border
    }
    component Toggle: Switch {
        id: toggle
        implicitWidth: 60
        implicitHeight: 40
        indicator: Rectangle {
            x: toggle.leftPadding
            y: (toggle.height - height) / 2
            implicitWidth: 42; implicitHeight: 24
            radius: 12
            color: toggle.checked ? appearance.colors.accent : appearance.colors.surfaceAlt
            border.width: toggle.visualFocus ? 2 : 1
            border.color: toggle.visualFocus ? appearance.colors.accent : toggle.checked ? appearance.colors.accent : appearance.colors.border
            Rectangle {
                x: toggle.checked ? parent.width - width - 4 : 4
                anchors.verticalCenter: parent.verticalCenter
                width: 16; height: 16; radius: 8
                color: toggle.checked ? appearance.colors.accentText : appearance.colors.muted
            }
        }
        HoverHandler { cursorShape: appearance.pointerCursors ? Qt.PointingHandCursor : Qt.ArrowCursor }
    }
    component Label: Text {
        Layout.fillWidth: true
        textFormat: Text.PlainText
        color: appearance.colors.text
        font: appearance.uiFont
        wrapMode: Text.Wrap
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: workspace.density === "compact" ? 18 : 26
        spacing: 16
        RowLayout {
            Layout.fillWidth: true
            Heading { text: qsTr("Personalizza Kira"); Layout.topMargin: 0 }
            KButton { objectName: "diagnosticsButton"; text: qsTr("Diagnostica locale"); subtle: true; onClicked: settingsPage.diagnosticsRequested() }
        }
        Flow {
            objectName: "settingsCategories"
            Layout.fillWidth: true
            spacing: 6
            KButton { objectName: "settingsAppearanceCategory"; quietSelection: true; text: qsTr("Aspetto"); checked: settingsPage.selectedSection === 0; subtle: true; onClicked: settingsPage.showSection(0) }
            KButton { objectName: "settingsWorkCategory"; quietSelection: true; text: qsTr("Lavoro"); checked: settingsPage.selectedSection === 1; subtle: true; onClicked: settingsPage.showSection(1) }
            KButton { objectName: "settingsConnectionsCategory"; quietSelection: true; text: qsTr("Connessioni"); checked: settingsPage.selectedSection === 2; subtle: true; onClicked: settingsPage.showSection(2) }
            KButton { objectName: "settingsDataCategory"; quietSelection: true; text: qsTr("Dati locali"); checked: settingsPage.selectedSection === 3; subtle: true; onClicked: settingsPage.showSection(3) }
        }
        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: appearance.colors.border }
        ScrollView {
            id: pane
            objectName: "settingsPane"
            readonly property color muted: appearance.colors.muted
            readonly property bool serviceEditable: workspace.connected && !workspace.busy && !teams.busy
            property alias selectedSection: settingsPage.selectedSection
            function showSection(index) { settingsPage.showSection(index) }
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: Math.max(0, pane.availableWidth - 16)
                spacing: 0
                ColumnLayout {
                    objectName: "settingsAppearanceSection"
                    readonly property int settingsCategory: 0
                    visible: settingsPage.selectedSection === 0
                    Layout.fillWidth: true
                    spacing: 14
                    Label { objectName: "languageLabel"; text: qsTr("Lingua dell’interfaccia") }
                    KComboBox {
                        objectName: "languageChoice"
                        Layout.fillWidth: true
                        model: localization.languages
                        textRole: "name"
                        valueRole: "code"
                        currentIndex: localization.languages.findIndex(function(language) { return language.code === localization.language })
                        // A refused language leaves the previous one active: show that one again.
                        onActivated: {
                            localization.setLanguage(currentValue)
                            currentIndex = Qt.binding(function() { return localization.languages.findIndex(function(language) { return language.code === localization.language }) })
                        }
                        Accessible.name: qsTr("Lingua dell’interfaccia")
                    }
                    AppearancePane { Layout.fillWidth: true }
                    Item { implicitHeight: 20 }
                }
                ColumnLayout {
                    objectName: "settingsWorkSection"
                    readonly property int settingsCategory: 1
                    visible: settingsPage.selectedSection === 1
                    Layout.fillWidth: true
                    spacing: 14
                    Heading { text: qsTr("Anima di Kira") }
                    Note { text: qsTr("Crea le tue Kira: identità, modo di collaborare e ricordi con fonti. Scegli quale usare in ogni chat.") }
                    KButton {
                        objectName: "settingsSoulsButton"
                        text: qsTr("Le mie Kira")
                        enabled: workspace.connected
                        onClicked: settingsPage.soulsRequested()
                    }
                    Rule {}
                    Heading { text: qsTr("Conversazione") }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: pane.availableWidth < 560 ? 1 : 2
                        columnSpacing: 20
                        rowSpacing: 12
                        Label { text: qsTr("Densità dell’interfaccia") }
                        KComboBox {
                            palette.dark: pane.muted
                            objectName: "densityChoice"
                            Layout.preferredWidth: 230
                            Layout.fillWidth: pane.availableWidth < 560
                            model: [qsTr("Comoda"), qsTr("Compatta")]
                            currentIndex: workspace.density === "compact" ? 1 : 0
                            onActivated: workspace.setDensity(currentIndex === 1 ? "compact" : "comfortable")
                            Accessible.name: qsTr("Densità dell'interfaccia")
                        }
                        Label { text: qsTr("Barra laterale") }
                        KComboBox {
                            palette.dark: pane.muted
                            objectName: "sidebarModeChoice"
                            Layout.preferredWidth: 230
                            Layout.fillWidth: pane.availableWidth < 560
                            model: [qsTr("Intera"), qsTr("Solo icone"), qsTr("Nascosta")]
                            currentIndex: Math.max(0, ["full", "rail", "hidden"].indexOf(workspace.sidebarMode))
                            onActivated: workspace.setSidebarMode(["full", "rail", "hidden"][currentIndex])
                            Accessible.name: qsTr("Barra laterale")
                        }
                        Label { text: qsTr("Etichette accanto alle icone degli strumenti") }
                        Toggle {
                            objectName: "toolLabelsChoice"
                            checked: workspace.toolLabels
                            onToggled: workspace.setToolLabels(checked)
                            Accessible.name: qsTr("Etichette accanto alle icone degli strumenti")
                        }
                        Label { text: qsTr("Apri le opzioni del modello con la chat") }
                        Toggle {
                            objectName: "modelControlsChoice"
                            checked: workspace.modelControlsVisible
                            onToggled: workspace.setModelControlsVisible(checked)
                            Accessible.name: qsTr("Apri le opzioni del modello con la chat")
                        }
                        Label { text: qsTr("Mostra riepiloghi del ragionamento") }
                        Toggle {
                            objectName: "reasoningSummariesChoice"
                            checked: workspace.uiPreferences.show_reasoning_summaries
                            onToggled: workspace.setUiPreference("show_reasoning_summaries", checked)
                            Accessible.name: qsTr("Mostra riepiloghi del ragionamento")
                        }
                        Label { text: qsTr("Mostra il pannello Dettagli") }
                        Toggle {
                            objectName: "detailsChoice"
                            checked: workspace.uiPreferences.details_visible
                            onToggled: workspace.setUiPreference("details_visible", checked)
                            Accessible.name: qsTr("Mostra il pannello Dettagli")
                        }
                        Label { text: qsTr("Mostra il suggerimento «Il prossimo passo»") }
                        Toggle {
                            objectName: "resumeHintChoice"
                            checked: workspace.resumeHintVisible
                            onToggled: workspace.setUiPreference("resume_hint_visible", checked)
                            Accessible.name: qsTr("Mostra il suggerimento «Il prossimo passo»")
                        }
                    }
                    Rule {}
                    Heading { text: qsTr("Finestra e file") }
                    Label { text: qsTr("Chiedi conferma prima di chiudere la finestra") }
                    KComboBox {
                            palette.dark: pane.muted
                        objectName: "closePolicyChoice"
                        Layout.fillWidth: true
                        model: [qsTr("Quando c’è lavoro in corso"), qsTr("Sempre"), qsTr("Mai")]
                        currentIndex: ["active", "always", "never"].indexOf(workspace.uiPreferences.confirm_close)
                        onActivated: workspace.setUiPreference("confirm_close", ["active", "always", "never"][currentIndex])
                        Accessible.name: qsTr("Conferma prima di chiudere la finestra")
                    }
                    Note { text: qsTr("Chiudere la finestra lascia il lavoro attivo nel servizio. Per interromperlo usa Ferma; arrestare il computer o WSL interrompe anche il servizio.") }
                    Label { text: qsTr("Apri i file con") }
                    KComboBox {
                            palette.dark: pane.muted
                        objectName: "fileOpenerChoice"
                        Layout.fillWidth: true
                        model: [qsTr("Applicazione predefinita del sistema"), "Visual Studio Code"]
                        currentIndex: workspace.uiPreferences.file_opener === "vscode" ? 1 : 0
                        onActivated: workspace.setUiPreference("file_opener", currentIndex === 1 ? "vscode" : "system")
                        Accessible.name: qsTr("Applicazione per aprire i file")
                    }
                    Note { text: qsTr("Si applica ai file locali aperti dai collegamenti e dagli artefatti. Visual Studio Code richiede il comando code disponibile sul computer.") }
                    Rule {}
                    Heading { text: qsTr("Permessi dei nuovi progetti") }
                    KComboBox {
                            palette.dark: pane.muted
                        objectName: "permissionDefaultChoice"
                        Layout.fillWidth: true
                        model: [qsTr("Sola lettura"), qsTr("Scrittura nel progetto"), qsTr("Host fidato · comandi come il tuo utente"), qsTr("Accesso completo")]
                        currentIndex: ["read_only", "project_write", "trusted_host", "full_access"].indexOf(workspace.uiPreferences.default_permission)
                        onActivated: workspace.setUiPreference("default_permission", ["read_only", "project_write", "trusted_host", "full_access"][currentIndex])
                        Accessible.name: qsTr("Permessi iniziali dei nuovi progetti")
                    }
                    Note {
                        objectName: "defaultPermissionDescription"
                        text: qsTr("Preselezione per la creazione dei nuovi progetti: puoi modificarla prima di crearli. I permessi dei progetti esistenti restano invariati.")
                    }
                    Note {
                        objectName: "defaultPermissionWarning"
                        text: workspace.uiPreferences.default_permission === "full_access"
                            ? qsTr("Consenti lettura e scrittura anche fuori dal progetto e comandi come il tuo utente. Restano i limiti del sistema operativo, senza privilegi amministrativi automatici.")
                            : workspace.uiPreferences.default_permission === "trusted_host"
                            ? qsTr("Host fidato autorizza i comandi sul computer con i tuoi permessi. Gli strumenti file restano nel progetto; la cartella di lavoro non è una sandbox.")
                            : workspace.uiPreferences.default_permission === "project_write"
                            ? qsTr("Consenti lettura e scrittura dei file del progetto. Codex usa la propria sandbox workspace; i comandi Kira richiedono Host fidato o Accesso completo.")
                            : qsTr("Consenti la lettura dei file. Modifiche e comandi Kira vengono rifiutati dal servizio.")
                        color: ["trusted_host", "full_access"].indexOf(workspace.uiPreferences.default_permission) >= 0 ? appearance.colors.warning : pane.muted
                    }
                    Item { implicitHeight: 20 }
                }
                ColumnLayout {
                    objectName: "settingsConnectionsSection"
                    readonly property int settingsCategory: 2
                    visible: settingsPage.selectedSection === 2
                    Layout.fillWidth: true
                    spacing: 14
                    Heading { text: qsTr("Account ChatGPT") }
                    DirectAccountPane { objectPrefix: "settingsChatGPT" }
                    Rule {}
                    Heading { text: qsTr("Connessioni e limiti degli strumenti") }
                    Note {
                        objectName: "settingsBusyNotice"
                        text: workspace.busy || teams.busy
                            ? qsTr("C’è lavoro in corso. Connessioni e limiti saranno modificabili al termine; le preferenze dell’interfaccia restano disponibili.")
                            : qsTr("Queste impostazioni vengono applicate solo premendo Salva impostazioni.")
                    }
                    ColumnLayout {
                        objectName: "backendSettingsFields"
                        Layout.fillWidth: true
                        enabled: pane.serviceEditable
                        spacing: 12
                        Label { text: qsTr("Endpoint Ollama") }
                        TextField {
                            id: endpointInput
                            objectName: "settingsEndpoint"
                            text: workspace.settings.ollama_url || "http://127.0.0.1:11434"
                            Layout.fillWidth: true
                            selectByMouse: true
                            Accessible.name: qsTr("Endpoint Ollama")
                        }
                        Note { text: qsTr("Un indirizzo remoto invia il contesto a quel server. La modifica è una tua scelta esplicita.") }
                        Label { text: qsTr("Eseguibile Codex") }
                        TextField {
                            id: binaryInput
                            objectName: "settingsCodexBinary"
                            text: workspace.settings.codex_binary || "codex"
                            Layout.fillWidth: true
                            selectByMouse: true
                            Accessible.name: qsTr("Eseguibile Codex")
                        }
                        GridLayout {
                            Layout.fillWidth: true
                            columns: pane.availableWidth < 600 ? 1 : 2
                            columnSpacing: 20
                            rowSpacing: 12
                            Label { text: qsTr("Timeout strumenti (secondi)") }
                            SpinBox {
                                id: timeoutInput
                                objectName: "settingsToolTimeout"
                                from: 1; to: 86400; value: workspace.settings.tool_timeout || 120; editable: true
                                Layout.preferredWidth: 190
                                Accessible.name: qsTr("Timeout strumenti in secondi")
                            }
                            Label { text: qsTr("Limite turni strumenti (0 = illimitati)") }
                            SpinBox {
                                id: roundsInput
                                objectName: "settingsToolRounds"
                                from: 0; to: 10000; value: workspace.settings.max_tool_rounds || 0; editable: true
                                Layout.preferredWidth: 190
                                Accessible.name: qsTr("Limite turni strumenti, zero significa illimitati")
                            }
                            Label { text: qsTr("Output massimo per esecuzione (byte, fino a 16 MiB)") }
                            SpinBox {
                                id: outputInput
                                objectName: "settingsOutputLimit"
                                from: 4096; to: 16777216; stepSize: 4096
                                value: workspace.settings.max_output_bytes || 1048576; editable: true
                                Layout.preferredWidth: 190
                                Accessible.name: qsTr("Output massimo per esecuzione in byte")
                            }
                        }
                        KButton {
                            objectName: "settingsSaveButton"
                            text: qsTr("Salva impostazioni")
                            primary: true
                            onClicked: workspace.saveSettings(endpointInput.text, binaryInput.text, timeoutInput.value, roundsInput.value, outputInput.value)
                        }
                    }
                    Rule {}
                    Heading { text: qsTr("Server MCP esterni") }
                    McpServersPane {}
                    Item { implicitHeight: 20 }
                }
                ColumnLayout {
                    objectName: "settingsDataSection"
                    readonly property int settingsCategory: 3
                    visible: settingsPage.selectedSection === 3
                    Layout.fillWidth: true
                    spacing: 14
                    Heading { text: qsTr("Dati locali e risorse") }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 10
                        KButton { objectName: "settingsBackupButton"; text: qsTr("Backup del database"); enabled: workspace.connected; onClicked: workspace.backup() }
                        KButton { objectName: "settingsResourcesButton"; text: qsTr("Aggiorna risorse"); enabled: workspace.connected; onClicked: workspace.refreshResources() }
                    }
                    ReadText { text: workspace.resourcesText; Layout.fillWidth: true; font: appearance.codeFont }
                    Rule {}
                    Heading { text: qsTr("Aggiornamenti") }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: 20
                        Label { text: qsTr("Cerca aggiornamenti all’avvio e ogni giorno") }
                        Toggle {
                            objectName: "checkUpdatesChoice"
                            checked: updates.enabled
                            onToggled: updates.setEnabled(checked)
                            Accessible.name: qsTr("Cerca aggiornamenti all’avvio e ogni giorno")
                        }
                    }
                    Note {
                        text: updates.kind === "source"
                              ? qsTr("Questa è una cartella dei sorgenti: Kira controlla il ramo remoto con git e aggiorna con git e uv.")
                              : updates.kind === "package"
                                ? qsTr("Kira chiede a GitHub l’ultima release, scarica il pacchetto per la tua versione di Ubuntu e lo installa solo se la firma è valida.")
                                : qsTr("Questa installazione non si aggiorna da Kira: usa la pagina delle release.")
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 10
                        KButton {
                            objectName: "settingsCheckUpdates"
                            text: updates.state === "checking" ? qsTr("Controllo in corso…") : qsTr("Cerca aggiornamenti")
                            iconName: "refresh"
                            enabled: updates.state !== "checking" && updates.state !== "updating"
                            onClicked: updates.check()
                        }
                        KButton {
                            objectName: "settingsOpenUpdate"
                            visible: updates.available
                            text: qsTr("Aggiorna a %1").arg(updates.latestVersion); primary: true
                            onClicked: settingsPage.updateRequested()
                        }
                    }
                    Note {
                        objectName: "settingsUpdateStatus"
                        visible: text.length > 0
                        text: updates.state === "checking" ? ""
                              : (updates.message.length ? updates.message
                                 : updates.available ? qsTr("È disponibile Kira %1.").arg(updates.latestVersion) : "")
                              + (updates.lastChecked.length ? (updates.message.length || updates.available ? "\n" : "")
                                 + qsTr("Ultimo controllo: %1").arg(updates.lastChecked) : "")
                    }
                    Rule {}
                    Heading { text: qsTr("Scorciatoie") }
                    Note { objectName: "settingsShortcuts"; text: qsTr("Ctrl+Invio · Invia messaggio\nCtrl+B · Barra laterale: intera, solo icone o nascosta\nCtrl+K · Cerca progetti e azioni\nCtrl+N · Nuovo progetto\nCtrl+L · Scrivi nella chat\nCtrl+Shift+N · Nuova chat nel progetto\nCtrl+Shift+P · Azioni rapide\nCtrl+Shift+R · Riprendi lavoro\nCtrl+Shift+Q · Apri le domande\nCtrl+Shift+C · Copia la conversazione") }
                    Note {
                        Layout.topMargin: 16
                        Layout.bottomMargin: 28
                        text: qsTr("Kira Desktop %1 · Linux amd64\nLa ripresa dopo un’interruzione usa gli esiti registrati e non riesegue automaticamente operazioni incerte.").arg(workspace.appVersion)
                    }
                    Item { implicitHeight: 20 }
                }
            }
        }
    }
}
