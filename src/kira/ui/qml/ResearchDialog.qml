import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

KiraPanel {
    id: dialog
    objectName: "researchDialog"
    title: qsTr("Ricerca del progetto")
    fallbackWidth: 920
    fallbackHeight: 840
    font: appearance.uiFont
    property string page: "list"
    property bool synchronizing: false
    property bool advanced: false
    property string sourceReturn: "editor"
    property string pendingAction: ""
    onPageChanged: pendingAction = ""
    KFormat { id: kformat }
    function indexFor(rows, value) {
        for (let i = 0; i < rows.length; ++i) if (rows[i].value === value) return i
        return 0
    }
    function syncEditor() {
        synchronizing = true
        let value = projectResearch.editor
        titleInput.text = value.title || ""
        bodyInput.text = value.body || ""
        scopeInput.text = value.scope || ""
        criteriaInput.text = value.criteria || ""
        procedureInput.text = value.procedure || ""
        limitationsInput.text = value.limitations || ""
        reasonInput.text = value.reason || ""
        dependencyInput.text = value.dependency_paths || ""
        synchronizing = false
    }
    function editField(name, value) { if (!synchronizing) projectResearch.setField(name, value) }
    function showSource(ref, back) { sourceReturn = back; projectResearch.readSource(ref); page = "source" }
    onOpened: { pendingAction = ""; syncEditor(); projectResearch.open() }
    onClosed: { pendingAction = ""; projectResearch.hide() }
    Connections {
        target: projectResearch
        function onDismissed() { dialog.page = "list"; dialog.advanced = false; dialog.close() }
        function onEditorChanged() { dialog.syncEditor() }
    }
    footer: RowLayout {
        spacing: 8
        KButton { objectName: "researchSave"; text: qsTr("Salva scheda"); visible: dialog.page === "editor"; primary: true; enabled: projectResearch.canEdit && dialog.pendingAction.length === 0; Layout.leftMargin: 16; Layout.bottomMargin: 12; onClicked: projectResearch.save() }
        Item { Layout.fillWidth: true }
        KButton { text: qsTr("Chiudi"); Layout.rightMargin: 16; Layout.bottomMargin: 12; onClicked: dialog.close() }
    }
    contentItem: ColumnLayout {
        spacing: 10
        RowLayout {
            Layout.fillWidth: true
            KButton { text: qsTr("← Registro"); visible: dialog.page !== "list"; enabled: !projectResearch.busy; onClicked: { dialog.page = "list"; projectResearch.refresh() } }
            ReadText { text: projectResearch.busy ? qsTr("Caricamento…") : dialog.page === "list" ? qsTr("Schede, fonti e prossimi passi del progetto") : ""; color: appearance.colors.muted; Layout.fillWidth: true; Layout.minimumWidth: 0 }
            KButton { text: dialog.compactPanel ? "↻" : qsTr("Aggiorna"); toolTipText: qsTr("Aggiorna"); Accessible.name: qsTr("Aggiorna"); enabled: projectResearch.available && !projectResearch.busy; onClicked: projectResearch.refresh() }
        }
        ReadText { objectName: "researchError"; text: projectResearch.error; visible: text.length > 0; color: appearance.colors.warning; Layout.fillWidth: true }
        Rectangle {
            visible: dialog.pendingAction.length > 0
            Layout.fillWidth: true; implicitHeight: confirmationContent.implicitHeight + 20
            color: appearance.colors.surfaceAlt; radius: 10; border.color: appearance.colors.warning
            ColumnLayout {
                id: confirmationContent
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10
                ReadText { text: dialog.pendingAction === "discard" ? qsTr("Scartare questa bozza?") : qsTr("Ricaricare la scheda salvata?"); font.bold: true; Layout.fillWidth: true }
                ReadText { text: dialog.pendingAction === "discard" ? qsTr("Le revisioni già salvate resteranno nel registro.") : qsTr("Le modifiche non salvate di questa scheda verranno sostituite."); Layout.fillWidth: true }
                RowLayout {
                    KButton { objectName: "researchCancelConfirmation"; text: qsTr("Annulla"); onClicked: dialog.pendingAction = "" }
                    KButton {
                        objectName: "researchConfirm"
                        text: dialog.pendingAction === "discard" ? qsTr("Scarta questa bozza") : qsTr("Ricarica versione salvata")
                        enabled: projectResearch.canEdit
                        onClicked: {
                            let action = dialog.pendingAction
                            dialog.pendingAction = ""
                            if (action === "discard") projectResearch.discardDraft()
                            else if (action === "reload") projectResearch.reloadRecord()
                        }
                    }
                }
            }
        }
        RowLayout {
            visible: projectResearch.report.pending_count > 0
            ReadText { text: qsTr("Richieste da verificare: %1").arg(projectResearch.report.pending_count); Layout.fillWidth: true; color: appearance.colors.warning }
            KButton { text: qsTr("Verifica ricevuta"); enabled: projectResearch.canEdit; onClicked: projectResearch.retryPending() }
        }
        ScrollView {
            id: scroll; objectName: "researchScroll"
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true
            contentWidth: availableWidth; rightPadding: 14
            ScrollBar.vertical: KScrollBar { parent: scroll; x: scroll.width - width; y: 0; height: scroll.height; orientation: Qt.Vertical }
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: scroll.availableWidth; spacing: 12
                ColumnLayout {
                    visible: dialog.page === "list"; Layout.fillWidth: true; spacing: 12
                    ReadText { objectName: "researchIntro"; text: qsTr("Conserva ciò che sai, le prove e le questioni aperte. Aggiungere una scheda non esegue esperimenti e non autorizza strumenti."); Layout.fillWidth: true; color: appearance.colors.muted }
                    CheckBox {
                        id: sharing
                        objectName: "researchSharing"; text: qsTr("Condividi il registro con le chat del progetto")
                        Layout.fillWidth: true
                        checked: projectResearch.report.settings.enabled; enabled: projectResearch.canEdit
                        onClicked: projectResearch.configure(checked)
                        font: appearance.uiFont; palette.windowText: appearance.colors.text
                        contentItem: Text { text: sharing.text; font: sharing.font; color: appearance.colors.text; wrapMode: Text.Wrap; leftPadding: sharing.indicator.width + sharing.spacing; verticalAlignment: Text.AlignVCenter }
                    }
                    ReadText { text: qsTr("La condivisione è facoltativa. Include le schede accettate e le fonti collegate; disattivarla ferma nuovi invii, ma non ritira il contesto già ricevuto dal provider."); color: appearance.colors.muted; Layout.fillWidth: true }
                    KButton { text: qsTr("Leggi il contesto condiviso"); enabled: projectResearch.available && !projectResearch.busy; onClicked: { dialog.page = "context"; projectResearch.readContext() } }
                    Flow {
                        Layout.fillWidth: true; spacing: 6
                        Repeater {
                            model: [{value: "knowledge", label: qsTr("Cosa sappiamo")}, {value: "evidence", label: qsTr("Prove e tentativi")}, {value: "next", label: qsTr("Prossimo passo")}, {value: "proposals", label: qsTr("Proposte")}, {value: "all", label: qsTr("Tutte") }]
                            delegate: KButton { required property var modelData; text: modelData.label; checked: projectResearch.report.view === modelData.value; enabled: !projectResearch.busy; onClicked: projectResearch.setView(modelData.value) }
                        }
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: dialog.compactPanel ? 2 : 3
                        TextField { id: searchInput; objectName: "researchSearch"; Layout.fillWidth: true; placeholderText: qsTr("Cerca nel registro"); font: appearance.uiFont; onAccepted: projectResearch.search(text) }
                        KButton { text: qsTr("Cerca"); enabled: !projectResearch.busy; onClicked: projectResearch.search(searchInput.text) }
                        KButton { objectName: "researchNew"; text: qsTr("Nuova scheda di ricerca"); primary: true; Layout.columnSpan: dialog.compactPanel ? 2 : 1; enabled: projectResearch.canEdit; onClicked: { projectResearch.newRecord(); dialog.page = "editor"; dialog.syncEditor() } }
                    }
                    KButton { text: qsTr("Riprendi la bozza"); visible: projectResearch.editor.title.length > 0; enabled: !projectResearch.busy; onClicked: { dialog.page = "editor"; dialog.syncEditor() } }
                    ReadText { objectName: "researchEmpty"; visible: projectResearch.report.records.length === 0; text: qsTr("Nessuna scheda in questa vista. Inizia da un obiettivo o da una domanda, poi collega le prove disponibili."); color: appearance.colors.muted; Layout.fillWidth: true }
                    Repeater {
                        model: projectResearch.report.records
                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true; implicitHeight: recordContent.implicitHeight + 24
                            color: appearance.colors.surfaceAlt; radius: 10; border.color: appearance.colors.border
                            ColumnLayout {
                                id: recordContent; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12; spacing: 6
                                ReadText { text: modelData.title; font.bold: true; color: appearance.colors.accent; Layout.fillWidth: true }
                                ReadText { text: modelData.kind_label + " · " + modelData.assessment_label + " · " + modelData.status_label; Layout.fillWidth: true; color: modelData.assessment === "needs_recheck" ? appearance.colors.warning : appearance.colors.muted }
                                ReadText { text: modelData.origin_label + " · " + modelData.acceptance_label; Layout.fillWidth: true; color: appearance.colors.muted }
                                ReadText { text: modelData.preview; Layout.fillWidth: true }
                                ReadText { visible: modelData.body_truncated; text: qsTr("Anteprima: apri la scheda per leggere il testo completo."); color: appearance.colors.muted; Layout.fillWidth: true }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 8
                                    KButton { text: qsTr("Apri scheda"); enabled: !projectResearch.busy; onClicked: { projectResearch.editRecord(modelData.id); dialog.page = "editor" } }
                                    KButton { objectName: "researchAcceptProposal"; text: qsTr("Accetta"); visible: modelData.accepted === false; primary: true; enabled: projectResearch.canEdit; toolTipText: qsTr("Accetta la scheda nel registro condivisibile senza aprirla"); onClicked: projectResearch.acceptProposal(modelData.id) }
                                }
                            }
                        }
                    }
                    RowLayout {
                        KButton { text: qsTr("Precedente"); enabled: projectResearch.report.has_previous && !projectResearch.busy; onClicked: projectResearch.previousPage() }
                        KButton { text: qsTr("Successiva"); enabled: projectResearch.report.has_more && !projectResearch.busy; onClicked: projectResearch.nextPage() }
                    }
                }
                ColumnLayout {
                    visible: dialog.page === "editor"; Layout.fillWidth: true; spacing: 10
                    ReadText { text: projectResearch.editor.existing ? qsTr("Revisione %1 · %2").arg(projectResearch.editor.expected_revision).arg(projectResearch.editor.origin_label) : qsTr("Nuova scheda del progetto"); font.bold: true; color: appearance.colors.accent; Layout.fillWidth: true }
                    ReadText { text: projectResearch.editor.origin_detail; visible: text.length > 0; Layout.fillWidth: true; color: appearance.colors.muted }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: dialog.compactPanel ? 1 : 2
                        KComboBox { id: kindInput; objectName: "researchKind"; Layout.fillWidth: true; model: projectResearch.labels.kinds; currentIndex: dialog.indexFor(projectResearch.labels.kinds, projectResearch.editor.kind); textRole: "label"; valueRole: "value"; enabled: projectResearch.canEdit; onActivated: dialog.editField("kind", currentValue) }
                        KComboBox { id: statusInput; objectName: "researchStatus"; Layout.preferredWidth: 150; Layout.fillWidth: dialog.compactPanel; model: projectResearch.labels.statuses; currentIndex: dialog.indexFor(projectResearch.labels.statuses, projectResearch.editor.status); textRole: "label"; valueRole: "value"; enabled: projectResearch.canEdit; onActivated: dialog.editField("status", currentValue) }
                    }
                    TextField { id: titleInput; objectName: "researchTitle"; placeholderText: qsTr("Titolo della scheda"); Layout.fillWidth: true; font: appearance.uiFont; enabled: projectResearch.canEdit; onTextChanged: dialog.editField("title", text) }
                    TextArea { id: bodyInput; objectName: "researchBody"; placeholderText: qsTr("Descrivi l’obiettivo, la domanda, il tentativo o la conclusione."); Layout.fillWidth: true; Layout.preferredHeight: 120; wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText; font: appearance.uiFont; selectByMouse: true; enabled: projectResearch.canEdit; onTextChanged: dialog.editField("body", text) }
                    ReadText { text: qsTr("Valutazione"); font.bold: true; Layout.fillWidth: true }
                    KComboBox { id: assessmentInput; objectName: "researchAssessment"; Layout.fillWidth: true; model: projectResearch.labels.assessments; currentIndex: dialog.indexFor(projectResearch.labels.assessments, projectResearch.editor.assessment); textRole: "label"; valueRole: "value"; enabled: projectResearch.canEdit; onActivated: dialog.editField("assessment", currentValue) }
                    ReadText { text: qsTr("Una fonte documenta un’origine, non garantisce una conclusione. Supportato o contraddetto richiede ambito, criterio e almeno una fonte."); color: appearance.colors.muted; Layout.fillWidth: true }
                    CheckBox { text: qsTr("Valuto esplicitamente questa revisione"); Layout.fillWidth: true; checked: projectResearch.editor.reassess; enabled: projectResearch.canEdit; font: appearance.uiFont; palette.windowText: appearance.colors.text; onClicked: dialog.editField("reassess", checked); contentItem: Text { text: parent.text; font: parent.font; color: appearance.colors.text; wrapMode: Text.Wrap; leftPadding: parent.indicator.width + parent.spacing; verticalAlignment: Text.AlignVCenter } }
                    CheckBox { objectName: "researchAccepted"; text: qsTr("Accetta la scheda nel registro condivisibile"); Layout.fillWidth: true; checked: projectResearch.editor.accepted; enabled: projectResearch.canEdit; font: appearance.uiFont; palette.windowText: appearance.colors.text; onClicked: dialog.editField("accepted", checked); contentItem: Text { text: parent.text; font: parent.font; color: appearance.colors.text; wrapMode: Text.Wrap; leftPadding: parent.indicator.width + parent.spacing; verticalAlignment: Text.AlignVCenter } }
                    ReadText { text: qsTr("Accettare una proposta conserva l’autore originale. Le modifiche ai contenuti richiedono una nuova valutazione."); color: appearance.colors.muted; Layout.fillWidth: true }
                    TextArea { id: reasonInput; objectName: "researchReason"; visible: projectResearch.editor.existing || projectResearch.editor.reassess; placeholderText: qsTr("Motivo della revisione o della valutazione"); Layout.fillWidth: true; Layout.preferredHeight: 70; font: appearance.uiFont; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; enabled: projectResearch.canEdit; onTextChanged: dialog.editField("reason", text) }
                    RowLayout {
                        ReadText { text: qsTr("Fonti collegate"); font.bold: true; Layout.fillWidth: true }
                        KButton { objectName: "researchSources"; text: qsTr("Collega una fonte"); enabled: projectResearch.canEdit && projectResearch.editor.sources.length < 8; onClicked: { dialog.page = "sources"; projectResearch.openSources() } }
                    }
                    Repeater {
                        model: projectResearch.editor.sources
                        delegate: GridLayout {
                            required property var modelData; required property int index
                            Layout.fillWidth: true; columns: dialog.compactPanel ? 1 : 3
                            ReadText { text: qsTr("Fonte %1 · %2").arg(index + 1).arg(projectResearch.sourceKind(modelData.kind)); Layout.fillWidth: true }
                            KButton { text: qsTr("Leggi originale"); enabled: !projectResearch.busy; onClicked: dialog.showSource(modelData, "editor") }
                            KButton { text: qsTr("Rimuovi"); enabled: projectResearch.canEdit; onClicked: projectResearch.removeItem("sources", index) }
                        }
                    }
                    ReadText { visible: projectResearch.editor.sources.length === 0; text: qsTr("Nessuna fonte collegata."); color: appearance.colors.muted; Layout.fillWidth: true }
                    KButton { text: dialog.advanced ? qsTr("Nascondi dettagli della scheda") : qsTr("Ambito, criteri e dipendenze"); Layout.maximumWidth: scroll.availableWidth; checked: dialog.advanced; onClicked: dialog.advanced = !dialog.advanced }
                    ColumnLayout {
                        visible: dialog.advanced; Layout.fillWidth: true; spacing: 10
                        TextArea { id: scopeInput; objectName: "researchScope"; placeholderText: qsTr("Ambito: a quale versione, situazione o dato si applica?"); Layout.fillWidth: true; Layout.preferredHeight: 75; font: appearance.uiFont; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; enabled: projectResearch.canEdit; onTextChanged: dialog.editField("scope", text) }
                        TextArea { id: criteriaInput; objectName: "researchCriteria"; placeholderText: qsTr("Criterio: quale risultato permette di valutare la conclusione?"); Layout.fillWidth: true; Layout.preferredHeight: 75; font: appearance.uiFont; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; enabled: projectResearch.canEdit; onTextChanged: dialog.editField("criteria", text) }
                        TextArea { id: procedureInput; placeholderText: qsTr("Procedura del controllo o dell’esperimento"); Layout.fillWidth: true; Layout.preferredHeight: 90; font: appearance.uiFont; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; enabled: projectResearch.canEdit; onTextChanged: dialog.editField("procedure", text) }
                        TextArea { id: limitationsInput; placeholderText: qsTr("Limiti, condizioni e incertezze"); Layout.fillWidth: true; Layout.preferredHeight: 75; font: appearance.uiFont; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; enabled: projectResearch.canEdit; onTextChanged: dialog.editField("limitations", text) }
                        ReadText { text: qsTr("File da ricontrollare"); font.bold: true; Layout.fillWidth: true }
                        TextArea { id: dependencyInput; objectName: "researchDependencyPaths"; onTextChanged: dialog.editField("dependency_paths", text); placeholderText: qsTr("Percorsi relativi al progetto, uno per riga"); Layout.fillWidth: true; Layout.preferredHeight: 80; font: appearance.uiFont; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; enabled: projectResearch.canEdit }
                        KButton { text: qsTr("Registra le impronte dei file"); enabled: projectResearch.canEdit; onClicked: projectResearch.captureDependencies(dependencyInput.text) }
                        ReadText { text: qsTr("Il controllo legge solo i file dichiarati. Non è un monitoraggio continuo; impronte uguali non confermano automaticamente una conclusione."); Layout.fillWidth: true; color: appearance.colors.muted }
                        Repeater {
                            model: projectResearch.editor.dependencies
                            delegate: RowLayout {
                                required property var modelData; required property int index
                                Layout.fillWidth: true
                                ReadText { text: modelData.path + "\n" + modelData.sha256; Layout.fillWidth: true; color: appearance.colors.muted }
                                KButton { text: qsTr("Rimuovi"); enabled: projectResearch.canEdit; onClicked: projectResearch.removeItem("dependencies", index) }
                            }
                        }
                        KButton { text: qsTr("Ricontrolla i file registrati"); visible: projectResearch.editor.existing; enabled: projectResearch.canEdit; onClicked: { projectResearch.recheck(); dialog.page = "history" } }
                        KButton { text: qsTr("Collega un’altra scheda"); enabled: projectResearch.canEdit && projectResearch.editor.links.length < 8; onClicked: { dialog.page = "links"; projectResearch.openLinks() } }
                        Repeater {
                            model: projectResearch.editor.links
                            delegate: RowLayout {
                                required property var modelData; required property int index
                                Layout.fillWidth: true
                                ReadText { text: projectResearch.relationLabel(modelData.relation) + " · " + qsTr("Revisione %1").arg(modelData.revision); Layout.fillWidth: true }
                                KButton { text: qsTr("Leggi scheda"); enabled: !projectResearch.busy; onClicked: { projectResearch.readLinkedRevision(modelData.record_id, modelData.revision); dialog.page = "history" } }
                                KButton { text: qsTr("Rimuovi"); enabled: projectResearch.canEdit; onClicked: projectResearch.removeItem("links", index) }
                            }
                        }
                    }
                    Flow {
                        Layout.fillWidth: true; spacing: 8
                        KButton { text: qsTr("Revisioni precedenti"); visible: projectResearch.editor.existing; enabled: !projectResearch.busy; onClicked: { dialog.page = "history"; projectResearch.openHistory() } }
                        KButton { text: qsTr("Ricarica versione salvata"); visible: projectResearch.editor.existing; enabled: projectResearch.canEdit; onClicked: dialog.pendingAction = "reload" }
                        KButton { text: qsTr("Scarta questa bozza"); enabled: projectResearch.canEdit; onClicked: dialog.pendingAction = "discard" }
                    }
                }
                ColumnLayout {
                    visible: dialog.page === "sources"; Layout.fillWidth: true; spacing: 10
                    ReadText { text: qsTr("Scegli un’esecuzione e poi il risultato da collegare. Lo stato osservato resta distinto dalla tua valutazione."); Layout.fillWidth: true; color: appearance.colors.muted }
                    Flow {
                        Layout.fillWidth: true; spacing: 8
                        KButton { text: qsTr("← Scheda"); onClicked: dialog.page = "editor" }
                        KButton { text: qsTr("Tutte le esecuzioni"); visible: projectResearch.picker.run_id.length > 0; enabled: !projectResearch.busy; onClicked: projectResearch.openSources() }
                    }
                    Repeater {
                        model: projectResearch.picker.items
                        delegate: ColumnLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            ReadText { text: modelData.label; Layout.fillWidth: true }
                            ReadText { text: projectResearch.sourceDescription(modelData); color: appearance.colors.muted; Layout.fillWidth: true }
                            Flow {
                                Layout.fillWidth: true; spacing: 6
                                KButton { text: qsTr("Risultati dell’esecuzione"); visible: modelData.ref.kind === "run"; enabled: !projectResearch.busy; onClicked: projectResearch.openRunSources(modelData.ref.run_id) }
                                KButton { text: qsTr("Leggi originale"); enabled: !projectResearch.busy; onClicked: dialog.showSource(modelData.ref, "sources") }
                                KButton { text: qsTr("Collega"); enabled: projectResearch.canEdit && projectResearch.editor.sources.length < 8; onClicked: { projectResearch.addSource(modelData.ref); dialog.page = "editor" } }
                            }
                        }
                    }
                    ReadText { visible: projectResearch.picker.items.length === 0; text: qsTr("Nessuna esecuzione o fonte disponibile in questa pagina."); color: appearance.colors.muted; Layout.fillWidth: true }
                    RowLayout {
                        KButton { text: qsTr("Precedente"); enabled: projectResearch.picker.has_previous && !projectResearch.busy; onClicked: projectResearch.previousSources() }
                        KButton { text: qsTr("Successiva"); enabled: projectResearch.picker.has_more && !projectResearch.busy; onClicked: projectResearch.nextSources() }
                    }
                }
                ColumnLayout {
                    visible: dialog.page === "source"; Layout.fillWidth: true; spacing: 10
                    KButton { text: qsTr("← Indietro"); onClicked: dialog.page = dialog.sourceReturn }
                    ReadText { text: qsTr("Fonte originale"); font.bold: true; Layout.fillWidth: true }
                    ReadText { text: projectResearch.sourceStatus; color: appearance.colors.muted; Layout.fillWidth: true }
                    ReadText { text: projectResearch.sourceMetadata; visible: text.length > 0; color: appearance.colors.muted; Layout.fillWidth: true }
                    ReadText { visible: projectResearch.source.binary === true; text: qsTr("Fonte binaria: qui sono disponibili i metadati e l’esito della verifica dell’impronta."); Layout.fillWidth: true; color: appearance.colors.muted }
                    ReadText { visible: projectResearch.source.upstream_truncated === true; text: qsTr("Il contenuto originale registrato segnala una limitazione. Non è una copia completa dell’output."); Layout.fillWidth: true; color: appearance.colors.warning }
                    ReadText { objectName: "researchSourceText"; text: projectResearch.source.content; Layout.fillWidth: true }
                    RowLayout {
                        KButton { text: qsTr("Precedente"); enabled: projectResearch.source.has_previous && !projectResearch.busy; onClicked: projectResearch.previousSourcePage() }
                        KButton { text: qsTr("Successiva"); enabled: projectResearch.source.has_more && !projectResearch.busy; onClicked: projectResearch.nextSourcePage() }
                    }
                }
                ColumnLayout {
                    visible: dialog.page === "history"; Layout.fillWidth: true; spacing: 10
                    KButton { text: qsTr("← Scheda"); onClicked: { dialog.page = "editor"; dialog.syncEditor() } }
                    ReadText { text: qsTr("Revisioni e controlli conservati"); font.bold: true; Layout.fillWidth: true }
                    Repeater {
                        model: projectResearch.history.revisions
                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            ReadText { text: qsTr("Revisione %1").arg(modelData.revision) + " · " + (modelData.reason || kformat.timestamp(modelData.created_at)); Layout.fillWidth: true }
                            KButton { text: qsTr("Leggi"); enabled: !projectResearch.busy; onClicked: projectResearch.readRevision(modelData.revision) }
                        }
                    }
                    KButton { text: qsTr("Revisioni meno recenti"); enabled: projectResearch.history.has_more && !projectResearch.busy; onClicked: projectResearch.nextHistory() }
                    ReadText { objectName: "researchRevisionText"; text: projectResearch.revisionText; Layout.fillWidth: true }
                    Repeater {
                        model: projectResearch.history.detail.sources || []
                        delegate: KButton {
                            required property var modelData; required property int index
                            text: qsTr("Leggi la fonte %1 della revisione").arg(index + 1)
                            enabled: !projectResearch.busy
                            onClicked: dialog.showSource(modelData, "history")
                        }
                    }
                }
                ColumnLayout {
                    visible: dialog.page === "links"; Layout.fillWidth: true; spacing: 10
                    KButton { text: qsTr("← Scheda"); onClicked: dialog.page = "editor" }
                    KComboBox { id: relationInput; objectName: "researchRelation"; Layout.fillWidth: true; model: projectResearch.labels.relations; textRole: "label"; valueRole: "value" }
                    Repeater {
                        model: projectResearch.linkPage.records
                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            ReadText { text: modelData.title + " · " + qsTr("Revisione %1").arg(modelData.revision); Layout.fillWidth: true }
                            KButton { text: qsTr("Collega"); enabled: projectResearch.canEdit && modelData.id !== projectResearch.editor.record_id; onClicked: { projectResearch.addLink(modelData.id, modelData.revision, relationInput.currentValue); dialog.page = "editor" } }
                        }
                    }
                    RowLayout {
                        KButton { text: qsTr("Precedente"); enabled: projectResearch.linkPage.has_previous && !projectResearch.busy; onClicked: projectResearch.previousLinks() }
                        KButton { text: qsTr("Successiva"); enabled: projectResearch.linkPage.has_more && !projectResearch.busy; onClicked: projectResearch.nextLinks() }
                    }
                }
                ColumnLayout {
                    visible: dialog.page === "context"; Layout.fillWidth: true
                    ReadText { text: qsTr("Contesto condivisibile del registro"); font.bold: true; Layout.fillWidth: true }
                    ReadText { text: projectResearch.sharedContext.content; Layout.fillWidth: true }
                    ReadText { visible: projectResearch.sharedContext.truncated === true; text: qsTr("Il contesto è limitato. Le schede originali restano disponibili nel registro."); color: appearance.colors.warning; Layout.fillWidth: true }
                }
            }
        }
    }
}
