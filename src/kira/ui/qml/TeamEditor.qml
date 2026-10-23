import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
    id: dialog
    objectName: "teamEditor"
    title: qsTr("Configura il team")
    modal: true
    anchors.centerIn: parent
    width: Math.min(820, parent.width - 32)
    height: Math.min(800, parent.height - 32)
    readonly property color muted: appearance.colors.muted
    component SectionHeading: Text {
        Layout.fillWidth: true
        Layout.topMargin: 14
        color: appearance.colors.accent
        font: Qt.font({family: appearance.uiFont.family, pixelSize: appearance.uiFont.pixelSize + 1, weight: Font.DemiBold, italic: appearance.uiFont.italic})
        textFormat: Text.PlainText; wrapMode: Text.Wrap
    }
    background: Rectangle { color: appearance.colors.surface; radius: 16; border.color: appearance.colors.border }
    onOpened: { teams.prepareDraft(); goal.forceActiveFocus() }
    Connections { target: teams; function onTeamStarted() { dialog.close() } }

    ColumnLayout {
        anchors.fill: parent
        spacing: 12
        ScrollView {
            id: form
            objectName: "teamEditorForm"
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true
            ScrollBar.vertical.policy: ScrollBar.AlwaysOn
            ColumnLayout {
                width: Math.max(0, form.availableWidth - 12)
                spacing: 12
                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; text: qsTr("Un obiettivo, competenze coordinate."); textFormat: Text.PlainText; color: appearance.colors.text; font.pixelSize: appearance.uiFont.pixelSize + 5; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; text: qsTr("Tutti gli agenti usano il modello e il ragionamento scelti qui. Il servizio conserva messaggi, attività e risultati."); textFormat: Text.PlainText; color: muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
                SectionHeading { text: qsTr("Obiettivo del team"); Layout.topMargin: 4 }
                ScrollView {
                    Layout.fillWidth: true; Layout.preferredHeight: 105; clip: true
                    TextArea {
                        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
                        id: goal; objectName: "teamGoalInput"
                        text: teams.draft.goal || ""
                        placeholderText: qsTr("Descrivi il risultato da raggiungere e come verificarlo…")
                        wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText; selectByMouse: true
                        Accessible.name: qsTr("Obiettivo del team")
                        onTextChanged: if (activeFocus) teams.setDraftText("goal", text)
                    }
                }
                SectionHeading { text: qsTr("Composizione") }
                RowLayout {
                    KButton { objectName: "teamAutomaticButton"; quietSelection: true; text: qsTr("Kira organizza"); checked: teams.draft.mode === "automatic"; onClicked: teams.setMode("automatic"); Layout.fillWidth: true }
                    KButton { objectName: "teamManualButton"; quietSelection: true; text: qsTr("Configuro io"); checked: teams.draft.mode === "manual"; onClicked: teams.setMode("manual"); Layout.fillWidth: true }
                }
                Text {
                    font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic
                    text: teams.draft.mode === "automatic"
                          ? qsTr("Automatico: Kira legge il contesto, compone il team entro il massimo scelto e avvia gli incarichi dopo un piano valido.")
                          : qsTr("Manuale: il numero è esatto. Il primo membro è il coordinatore e può anche svolgere incarichi.")
                    textFormat: Text.PlainText; color: muted; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2)
                }
                GridLayout {
                    columns: form.availableWidth < 540 ? 1 : 2; Layout.fillWidth: true; columnSpacing: 15; rowSpacing: 9
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; text: teams.draft.mode === "automatic" ? qsTr("Massimo agenti (coordinatore incluso)") : qsTr("Agenti totali (coordinatore incluso)"); textFormat: Text.PlainText; color: appearance.colors.text; Layout.fillWidth: true; wrapMode: Text.Wrap }
                    SpinBox { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; objectName: "teamCountInput"; from: 1; to: 12; value: teams.draft.max_agents; editable: true; onValueModified: teams.setCount(value); Accessible.name: qsTr("Agenti totali, coordinatore incluso") }
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; text: qsTr("Concorrenza"); textFormat: Text.PlainText; color: appearance.colors.text }
                    SpinBox { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; objectName: "teamConcurrencyInput"; from: 1; to: Math.min(8, teams.draft.max_agents); value: teams.draft.concurrency; editable: true; onValueModified: teams.setConcurrency(value); Accessible.name: qsTr("Concorrenza massima") }

                }
                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; text: qsTr("COMPOSIZIONE MANUALE"); textFormat: Text.PlainText; color: appearance.colors.accent; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); font.letterSpacing: 1; visible: teams.draft.mode === "manual" }
                Repeater {
                    model: teams.draft.mode === "manual" ? teams.draft.members : []
                    delegate: Rectangle {
                        id: member
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true; implicitHeight: memberForm.implicitHeight + 24
                        radius: 10; color: appearance.colors.surfaceAlt; border.color: "transparent"
                        ColumnLayout {
                            id: memberForm; anchors.fill: parent; anchors.margins: 12; spacing: 8
                            Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; text: member.index === 0 ? qsTr("COORDINATORE · incluso nel totale") : member.modelData.id; textFormat: Text.PlainText; color: appearance.colors.accent; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3) }
                            RowLayout {
                                TextField { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; objectName: "teamMemberName_" + member.index; Layout.fillWidth: true; text: member.modelData.name; placeholderText: qsTr("Nome"); selectByMouse: true; onTextEdited: teams.setMember(member.index, "name", text); Accessible.name: qsTr("Nome agente %1").arg(member.index + 1) }
                                KComboBox {
                                    palette.dark: appearance.colors.muted
                                    objectName: "teamMemberAccess_" + member.index
                                    model: teams.canWrite ? [qsTr("Lettura"), qsTr("Scrittura")] : [qsTr("Lettura")]
                                    currentIndex: member.modelData.access === "write" ? 1 : 0
                                    displayText: member.modelData.access === "write" && !teams.canWrite ? qsTr("Scrittura non consentita") : currentText
                                    onActivated: teams.setMember(member.index, "access", currentIndex === 1 ? "write" : "read")
                                    Accessible.name: qsTr("Accesso agente %1").arg(member.index + 1)
                                }
                            }
                            TextField { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; objectName: "teamMemberRole_" + member.index; Layout.fillWidth: true; text: member.modelData.role; placeholderText: qsTr("Specializzazione"); selectByMouse: true; onTextEdited: teams.setMember(member.index, "role", text); Accessible.name: qsTr("Specializzazione agente %1").arg(member.index + 1) }
                            TextArea { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; objectName: "teamMemberInstructions_" + member.index; Layout.fillWidth: true; Layout.preferredHeight: 75; text: member.modelData.instructions; placeholderText: qsTr("Responsabilità e istruzioni"); textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; selectByMouse: true; onTextChanged: if (activeFocus) teams.setMember(member.index, "instructions", text); Accessible.name: qsTr("Istruzioni agente %1").arg(member.index + 1) }
                        }
                    }
                }
                SectionHeading { text: qsTr("Modello e limiti") }
                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; text: qsTr("Provider e modello per tutto il team"); textFormat: Text.PlainText; color: appearance.colors.accent }
                KComboBox {
                    objectName: "teamBackendInput"
                    Layout.fillWidth: true
                    model: ["Codex", "Ollama", qsTr("OpenAI diretto · ChatGPT"), qsTr("DeepSeek Account")]
                    currentIndex: teams.draft.backend === "ollama" ? 1 : teams.draft.backend === "openai_chatgpt" ? 2 : teams.draft.backend === "deepseek_account" ? 3 : 0
                    onActivated: teams.setBackend(["codex", "ollama", "openai_chatgpt", "deepseek_account"][currentIndex])
                    enabled: !workspace.busy && !teams.busy
                    Accessible.name: qsTr("Provider condiviso dal team")
                }
                KComboBox {
                    palette.dark: appearance.colors.muted
                    id: model; objectName: "teamModelInput"
                    Layout.fillWidth: true; model: teams.models; textRole: "name"; valueRole: "id"; editable: true
                    currentIndex: indexOfValue(teams.draft.model)
                    displayText: currentIndex < 0 ? (teams.draft.model || qsTr("Scegli un modello…")) : currentText
                    onActivated: teams.setDraftText("model", String(currentValue))
                    onAccepted: if (editText.trim()) teams.setDraftText("model", editText.trim())
                    Accessible.name: qsTr("Modello condiviso dal team")
                    Binding { target: model.contentItem; property: "placeholderText"; value: qsTr("Seleziona o scrivi il modello") }
                    Binding { target: model; property: "editText"; value: teams.draft.model || ""; when: !model.activeFocus }
                    Connections { target: model.contentItem; function onTextEdited() { teams.setDraftText("model", model.contentItem.text) } }
                }
                RowLayout {
                    Layout.fillWidth: true
                    KComboBox {
                        palette.dark: appearance.colors.muted
                        objectName: "teamEffortInput"; Layout.fillWidth: true
                        model: [qsTr("Predefinito del provider")].concat(teams.efforts)
                        currentIndex: Math.max(0, teams.efforts.indexOf(teams.draft.reasoning_effort) + 1)
                        displayText: currentIndex === 0 && teams.draft.reasoning_effort ? qsTr("%1 · salvato").arg(teams.draft.reasoning_effort) : currentText
                        onActivated: teams.setDraftText("reasoning_effort", currentIndex === 0 ? "" : currentText)
                        Accessible.name: qsTr("Ragionamento condiviso dal team")
                    }
                    TextField {
                        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
                        objectName: "teamServiceTierInput"; Layout.fillWidth: true; visible: teams.draft.backend === "codex"
                        text: teams.draft.service_tier || ""; placeholderText: qsTr("Service tier · default provider")
                        selectByMouse: true; onTextEdited: teams.setDraftText("service_tier", text)
                        Accessible.name: qsTr("Service tier facoltativo condiviso dal team")
                    }
                }
                RowLayout {
                    KButton { text: qsTr("Aggiorna catalogo"); subtle: true; onClicked: workspace.discoverForTeam(teams.draft.backend) }
                    KButton { text: qsTr("Accedi con Codex"); visible: teams.draft.backend === "codex"; subtle: true; onClicked: workspace.login() }
                }
                DirectAccountPane { objectPrefix: "teamChatGPT"; visible: teams.draft.backend === "openai_chatgpt" }
                DeepSeekAccountPane { visible: teams.draft.backend === "deepseek_account" }
                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; text: teams.providerText; textFormat: Text.PlainText; color: muted; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
                GridLayout {
                    Layout.fillWidth: true; columns: form.availableWidth < 540 ? 1 : 2
                    columnSpacing: 15; rowSpacing: 9
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; text: qsTr("Round di coordinamento · 0 = illimitati"); textFormat: Text.PlainText; color: appearance.colors.text; Layout.fillWidth: true; wrapMode: Text.Wrap }
                    SpinBox { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; objectName: "teamRoundLimitInput"; from: 0; to: 10000; value: teams.draft.round_limit; editable: true; onValueModified: teams.setRoundLimit(value); Accessible.name: qsTr("Limite round, zero illimitati") }
                }
                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; text: qsTr("Il limite conta i round del coordinatore, non il ragionamento del modello. Le letture indipendenti possono procedere insieme; la scrittura è esclusiva."); textFormat: Text.PlainText; color: muted; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
                Text { objectName: "teamPermissionDescription"; font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; text: workspace.project.permission === "full_access" ? qsTr("Permessi progetto: Accesso completo. Gli agenti con Scrittura possono modificare file anche fuori dal progetto ed eseguire comandi come il tuo utente. La pianificazione e gli incarichi in Sola lettura restano limitati al progetto.") : workspace.project.permission === "trusted_host" ? qsTr("Permessi progetto: Host fidato. Gli agenti con Scrittura possono eseguire comandi come il tuo utente; i ruoli non sono una sandbox.") : teams.canWrite ? qsTr("Permessi progetto: Scrittura nel progetto. Ogni agente rispetta l'accesso assegnato; il coordinatore legge per pianificare.") : qsTr("Permessi progetto: Sola lettura. Tutti gli agenti lavorano senza modificare i file."); textFormat: Text.PlainText; color: muted; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
            }
        }
        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; text: teams.error; textFormat: Text.PlainText; color: appearance.colors.danger; visible: text.length > 0; wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight; Layout.fillWidth: true }
        RowLayout {
            Item { Layout.fillWidth: true }
            KButton { text: qsTr("Chiudi"); subtle: true; onClicked: dialog.close() }
            KButton { objectName: "startTeamButton"; text: teams.busy ? qsTr("Team in corso") : qsTr("Avvia team"); primary: true; enabled: workspace.connected && teams.hasProject && !teams.busy && !workspace.busy; onClicked: teams.startTeam() }
        }
    }
}
