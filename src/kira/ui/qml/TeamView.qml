import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    objectName: "teamView"
    property bool followMessages: true
    readonly property color muted: appearance.colors.muted
    readonly property color accent: appearance.colors.accent
    KFormat { id: kformat }
    function memberLabel(memberId) {
        var member = teams.members.find(function(item) { return item.id === memberId })
        return member && member.name && member.name !== memberId ? member.name + " · " + memberId : memberId
    }
    function stateColor(state) {
        return ["failed", "interrupted", "cancelled"].indexOf(state) >= 0 ? appearance.colors.warning
            : ["running", "planning", "completed"].indexOf(state) >= 0 ? accent : muted
    }
    component TeamTab: TabButton {
        id: tab
        font: appearance.uiFont
        implicitHeight: Math.max(42, label.implicitHeight + 20)
        HoverHandler { cursorShape: appearance.pointerCursors && tab.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
        background: Rectangle {
            color: tab.hovered ? appearance.colors.hover : "transparent"
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: tab.checked ? 2 : 1; color: tab.checked ? appearance.colors.accent : appearance.colors.border }
        }
        contentItem: Text {
            id: label
            text: tab.text; font: tab.font; textFormat: Text.PlainText
            color: tab.checked ? appearance.colors.accent : appearance.colors.muted
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            wrapMode: Text.Wrap
        }
    }
    Component.onCompleted: teams.activate()
    Connections {
        target: teams
        function onMessagesChanged() {
            if (page.followMessages) Qt.callLater(function() { messages.positionViewAtEnd() })
        }
        function onMessageAccepted() { page.followMessages = true }
        function onEvidenceOpened() { evidenceDialog.open() }
        function onEvidenceChanged() { if (!teams.evidenceRunId && evidenceDialog.opened) evidenceDialog.close() }
    }
    TeamEditor {
        id: editor; parent: Overlay.overlay
        onClosed: configure.forceActiveFocus()
    }
    Dialog {
        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
        id: goalDialog; objectName: "teamGoalDialog"; parent: Overlay.overlay
        title: qsTr("Obiettivo del team"); modal: true; anchors.centerIn: parent
        width: Math.min(820, parent.width - 32); height: Math.min(680, parent.height - 32)
        background: Rectangle { color: appearance.colors.surface; radius: 12; border.color: appearance.colors.border }
        standardButtons: Dialog.Close
        contentItem: ScrollView {
            id: goalScroll
            implicitWidth: 600; implicitHeight: 400
            contentWidth: availableWidth; clip: true
            ScrollBar.vertical.policy: ScrollBar.AlwaysOn
            ReadText { objectName: "teamFullGoal"; width: goalScroll.availableWidth; text: teams.team.goal || "" }
        }
    }
    Dialog {
        id: evidenceDialog; objectName: "teamEvidenceDialog"; parent: Overlay.overlay
        title: qsTr("Evidenza dell'esecuzione"); modal: true; anchors.centerIn: parent
        font: appearance.uiFont
        width: Math.min(1060, parent.width - 32); height: Math.min(820, parent.height - 32)
        property bool readable: true
        property int fontZoom: 0
        property var collapsedEvents: ({})
        readonly property int payloadFontSize: Math.max(10, Math.min(28, appearance.codeFont.pixelSize + fontZoom))
        function setReadable(value) {
            var anchor = evidenceList.indexAt(1, evidenceList.contentY + 1)
            readable = value
            if (anchor >= 0) Qt.callLater(function() { evidenceList.positionViewAtIndex(anchor, ListView.Beginning) })
        }
        function toggleEvent(sequence) {
            var updated = Object.assign({}, collapsedEvents)
            updated[sequence] = !updated[sequence]
            collapsedEvents = updated
        }
        background: Rectangle { color: appearance.colors.surface; radius: 12; border.color: appearance.colors.border }
        onOpened: collapsedEvents = ({})
        onClosed: teams.closeRunEvidence()
        contentItem: ColumnLayout {
            spacing: 12
            ReadText { text: qsTr("Esecuzione: %1").arg(teams.evidenceRunId); font.pixelSize: appearance.uiFont.pixelSize; color: muted; Layout.fillWidth: true }
            Text { font: appearance.uiFont; text: qsTr("Eventi registrati dal servizio. Gli esiti degli strumenti sono distinti dai messaggi del modello."); textFormat: Text.PlainText; color: muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                KButton { objectName: "evidenceReadableButton"; text: qsTr("Vista leggibile"); checked: evidenceDialog.readable; onClicked: evidenceDialog.setReadable(true); toolTipText: qsTr("Testo a capo e strutture annidate indentate, senza cambiare i dati registrati.") }
                KButton { objectName: "evidenceJsonButton"; text: qsTr("JSON originale"); checked: !evidenceDialog.readable; onClicked: evidenceDialog.setReadable(false) }
                Item { Layout.fillWidth: true }
                KButton { objectName: "evidenceFontSmaller"; text: "A−"; enabled: evidenceDialog.payloadFontSize > 10; onClicked: evidenceDialog.fontZoom--; Accessible.name: qsTr("Riduci il testo degli eventi") }
                KButton { objectName: "evidenceFontLarger"; text: "A+"; enabled: evidenceDialog.payloadFontSize < 28; onClicked: evidenceDialog.fontZoom++; Accessible.name: qsTr("Ingrandisci il testo degli eventi") }
            }
            ListView {
                id: evidenceList; objectName: "teamEvidenceList"
                model: teamEvidenceModel; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 12
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: KScrollBar { policy: ScrollBar.AlwaysOn }
                delegate: Rectangle {
                    id: eventCard
                    required property string title
                    required property string status
                    required property string timestampLabel
                    required property string body
                    required property int sequence
                    required property bool bodyTruncated
                    readonly property bool expanded: !evidenceDialog.collapsedEvents[String(sequence)]
                    readonly property string renderedBody: evidenceDialog.readable ? teams.readableEvidence(body) : body
                    readonly property bool readableTruncated: evidenceDialog.readable && !bodyTruncated && renderedBody.endsWith("\n…")
                    width: Math.max(0, evidenceList.width - 16)
                    implicitHeight: eventContent.implicitHeight + 24
                    radius: 8; color: appearance.colors.surfaceAlt; border.color: appearance.colors.border
                    ColumnLayout {
                        id: eventContent
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 12; spacing: 12
                        RowLayout {
                            Layout.fillWidth: true; spacing: 10
                            KButton { objectName: "evidenceToggle_" + eventCard.sequence; text: eventCard.expanded ? "−" : "+"; subtle: true; implicitWidth: 28; onClicked: evidenceDialog.toggleEvent(String(eventCard.sequence)); Accessible.name: eventCard.expanded ? qsTr("Comprimi evento %1").arg(eventCard.sequence) : qsTr("Espandi evento %1").arg(eventCard.sequence) }
                            Text { objectName: "evidenceTitle_" + eventCard.sequence; text: "#" + eventCard.sequence + "  " + eventCard.title; textFormat: Text.PlainText; color: accent; font.family: appearance.uiFont.family; font.pixelSize: appearance.uiFont.pixelSize; font.weight: Font.DemiBold; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            KButton { objectName: "evidenceCopy_" + eventCard.sequence; text: qsTr("Copia JSON"); subtle: true; enabled: !eventCard.bodyTruncated; onClicked: workspace.copy(eventCard.body); toolTipText: qsTr("Copia il payload originale di questo evento.") }
                        }
                        ReadText { objectName: "evidenceTime_" + eventCard.sequence; text: eventCard.timestampLabel; color: muted; font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 1); Layout.fillWidth: true; Accessible.description: eventCard.status; HoverHandler { id: timestampHover } ToolTip.visible: timestampHover.hovered; ToolTip.text: eventCard.status }
                        ReadText { objectName: "evidenceBody_" + eventCard.sequence; visible: eventCard.expanded; color: appearance.colors.codeText; text: eventCard.renderedBody; Layout.fillWidth: true; font.pixelSize: evidenceDialog.payloadFontSize; font.family: appearance.codeFont.family; font.styleName: appearance.codeFont.styleName; font.weight: appearance.codeFont.weight; font.italic: appearance.codeFont.italic }
                        Text { visible: eventCard.readableTruncated; text: qsTr("Vista leggibile abbreviata. Il JSON originale è disponibile."); color: muted; font: appearance.uiFont; textFormat: Text.PlainText; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        Text { visible: eventCard.bodyTruncated; text: qsTr("Anteprima abbreviata: il payload completo resta negli eventi registrati. La copia JSON è disabilitata per questa anteprima."); color: appearance.colors.warning; font: appearance.uiFont; textFormat: Text.PlainText; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    }
                }
            }
            Text { font: appearance.uiFont; text: teams.evidenceError || teams.evidenceNotice; textFormat: Text.PlainText; visible: text.length > 0; color: teams.evidenceError ? appearance.colors.danger : muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
            RowLayout {
                KButton { objectName: "teamLoadRunEvidence"; text: teams.evidenceLoading ? qsTr("Caricamento…") : teams.evidenceMore ? qsTr("Carica altri eventi") : qsTr("Aggiorna eventi"); enabled: workspace.connected && !teams.evidenceLoading; onClicked: teams.loadRunEvidence() }
                Item { Layout.fillWidth: true }
                KButton { text: qsTr("Chiudi"); onClicked: evidenceDialog.close() }
            }
        }
    }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: workspace.density === "compact" ? 14 : 22
        spacing: 10
        RowLayout {
            Layout.fillWidth: true
            KComboBox {
                palette.dark: appearance.colors.muted
                objectName: "teamHistory"
                Layout.fillWidth: true; model: teamListModel; textRole: "label"; valueRole: "recordId"
                currentIndex: indexOfValue(teams.selectedId)
                displayText: currentIndex < 0 ? qsTr("Nessun team selezionato") : currentText
                onActivated: teams.selectTeam(String(currentValue))
                Accessible.name: qsTr("Cronologia dei team del progetto")
            }
            KButton { id: configure; objectName: "configureTeamButton"; text: qsTr("Nuovo team"); primary: true; enabled: teams.hasProject; onClicked: editor.open() }
            KButton { text: "↻"; implicitWidth: Math.ceil(38 * appearance.uiFont.pixelSize / 13); onClicked: { teams.refreshTeams(); teams.refresh() } Accessible.name: qsTr("Aggiorna team") }
        }
        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; text: teams.error; textFormat: Text.PlainText; color: appearance.colors.danger; visible: text.length > 0; wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight; Layout.fillWidth: true }
        RowLayout {
            Layout.fillWidth: true
            Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; id: goalPreview; objectName: "teamGoalPreview"; text: teams.team.goal || qsTr("Organizza il lavoro: scegli gli agenti oppure lascia a Kira la composizione del team."); textFormat: Text.PlainText; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; color: appearance.colors.text; font.pixelSize: appearance.uiFont.pixelSize + 3; Layout.fillWidth: true }
            KButton { objectName: "teamReadGoal"; text: qsTr("Leggi obiettivo"); subtle: true; visible: goalPreview.truncated; onClicked: goalDialog.open() }
        }
        RowLayout {
            Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; objectName: "teamStateText"; text: teams.stateText; textFormat: Text.PlainText; color: page.stateColor(teams.team.state); Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1) }
            KButton { objectName: "cancelTeamButton"; text: qsTr("Ferma team"); destructive: true; visible: teams.canCancel; enabled: workspace.connected; onClicked: teams.cancelTeam() }
        }
        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; objectName: "teamConfigurationText"; text: teams.team.id ? localization.format(qsTr("%1 · %2 · ragionamento %3 · concorrenza %4"), [kformat.providerLabel(teams.team.backend || "codex"), teams.team.model, (teams.team.settings || {}).reasoning_effort || qsTr("Predefinito del provider"), teams.team.concurrency]) : qsTr("Configurazione esplicita: modello e ragionamento sono condivisi da tutti gli agenti."); textFormat: Text.PlainText; color: muted; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; text: teams.team.parent_team_id ? qsTr("Continuazione del team %1 · gli incarichi precedenti non vengono rieseguiti automaticamente.").arg(teams.team.parent_team_id) : ""; textFormat: Text.PlainText; visible: text.length > 0; color: muted; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
        TabBar {
            id: tabs; objectName: "teamTabs"; Layout.fillWidth: true
            background: Item {}
            TeamTab { text: qsTr("Agenti e incarichi") }
            TeamTab { text: qsTr("Chat condivisa") }
            TeamTab { text: qsTr("Risultati") }
            TeamTab { text: qsTr("Autorizzazioni (%1)").arg(teams.approvals.length); Accessible.name: qsTr("Coda autorizzazioni del team") }
        }
        StackLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; currentIndex: tabs.currentIndex
            ScrollView {
                id: agentsScroll; objectName: "teamRosterScroll"; clip: true; contentWidth: availableWidth
                ColumnLayout {
                    width: Math.max(0, agentsScroll.availableWidth - 12); spacing: 8
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; text: qsTr("AGENTI"); textFormat: Text.PlainText; color: accent; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3) }
                    Repeater {
                        model: teamMembersModel
                        delegate: Rectangle {
                            id: memberRow
                            required property var record
                            objectName: "teamAgentRow_" + record.id
                            Layout.fillWidth: true; implicitHeight: agentRow.implicitHeight + 24
                            color: "transparent"
                            Rectangle { anchors.left: parent.left; anchors.bottom: parent.bottom; width: parent.width; height: 1; color: appearance.colors.border }
                            Rectangle {
                                width: 28; height: 28; radius: 8; x: 2; y: 12
                                color: appearance.colors.surfaceAlt
                                Text { anchors.centerIn: parent; text: (memberRow.record.name || memberRow.record.id || "").slice(0, 1).toUpperCase(); textFormat: Text.PlainText; font: appearance.uiFont; color: page.stateColor(memberRow.record.state) }
                            }
                            ColumnLayout {
                                id: agentRow; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12; anchors.leftMargin: 42; spacing: 5
                                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; text: record.name + " · " + (record.state_label || qsTr("In attesa")); textFormat: Text.PlainText; color: appearance.colors.text; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: appearance.uiFont.pixelSize + 2 }
                                ReadText { text: record.role || ""; color: muted; Layout.fillWidth: true; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1) }
                                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; text: record.access === "write" ? qsTr("Accesso massimo: scrittura secondo il progetto") : qsTr("Accesso massimo: lettura"); textFormat: Text.PlainText; color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); Layout.fillWidth: true; wrapMode: Text.Wrap }
                                ReadText { text: record.output_preview || ""; visible: text.length > 0; Layout.fillWidth: true; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1) }
                                KButton { text: qsTr("Dettagli esecuzione"); subtle: true; visible: Boolean(record.run_id); onClicked: teams.openRunEvidence(record.run_id); Accessible.name: qsTr("Evidenza di %1").arg(record.name) }
                            }
                        }
                    }
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; objectName: "teamStepsHeading"; text: { const completed = teams.steps.filter(function(s) { return s.state === "completed" }).length; return localization.format(completed === 1 ? qsTr("INCARICHI · %1 completato su %2") : qsTr("INCARICHI · %1 completati su %2"), [completed, teams.steps.length]) } textFormat: Text.PlainText; color: accent; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); Layout.topMargin: 10 }
                    Repeater {
                        model: teamStepsModel
                        delegate: Rectangle {
                            id: taskRow
                            required property var record
                            objectName: "teamTaskRow_" + record.id
                            Layout.fillWidth: true; implicitHeight: taskColumn.implicitHeight + 20
                            color: "transparent"
                            Rectangle { x: 15; y: 0; width: 1; height: parent.height; color: appearance.colors.border }
                            Rectangle { x: 12; y: 17; width: 7; height: 7; radius: 3.5; color: page.stateColor(taskRow.record.state) }
                            ColumnLayout {
                                id: taskColumn; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10; anchors.leftMargin: 42; spacing: 5
                                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; text: record.title + " · " + record.state_label; textFormat: Text.PlainText; color: appearance.colors.text; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; objectName: "teamTaskAssignment_" + record.id; text: localization.format(qsTr("%1 · round %2"), [page.memberLabel(record.member_id), record.round_index]) + ((record.depends_on || []).length ? qsTr(" · dipende da: %1").arg(record.depends_on.join(", ")) : ""); textFormat: Text.PlainText; color: muted; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3) }
                                ReadText { text: record.error || ""; color: appearance.colors.danger; visible: text.length > 0; Layout.fillWidth: true; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1) }
                                KButton { text: qsTr("Dettagli esecuzione"); subtle: true; visible: Boolean(record.run_id); onClicked: teams.openRunEvidence(record.run_id); Accessible.name: qsTr("Evidenza incarico %1").arg(record.title) }
                            }
                        }
                    }
                }
            }
            ColumnLayout {
                spacing: 8
                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; text: qsTr("I messaggi sono registrati prima della consegna. Fornirli al contesto non significa che il modello li abbia letti. Un messaggio non riavvia un team terminato o in pausa."); textFormat: Text.PlainText; color: muted; wrapMode: Text.Wrap; Layout.fillWidth: true; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
                ListView {
                    id: messages; objectName: "teamMessages"
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 12
                    model: teamMessagesModel
                    ScrollBar.vertical: ScrollBar {}
                    onMovementStarted: page.followMessages = false
                    delegate: ColumnLayout {
                        required property string title
                        required property string body
                        required property string status
                        required property int index
                        width: messages.width; spacing: 5
                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; text: title; textFormat: Text.PlainText; color: accent; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; text: status; textFormat: Text.PlainText; color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3) }
                        ReadText { objectName: "teamMessage_" + index; text: body; Layout.fillWidth: true }
                    }
                }
                KButton { text: qsTr("↓ Ultimi messaggi"); visible: !page.followMessages; onClicked: { page.followMessages = true; messages.positionViewAtEnd() } }
                RowLayout {
                    KComboBox {
                        palette.dark: appearance.colors.muted
                        id: recipient; objectName: "teamMessageRecipient"
                        Layout.preferredWidth: 160
                        model: teamRecipientModel
                        textRole: "label"; valueRole: "recordId"
                        currentIndex: Math.max(0, indexOfValue(teams.recipientDraft))
                        onActivated: teams.setRecipientDraft(String(currentValue))
                        Accessible.name: qsTr("Destinatario del messaggio")
                    }
                    TextArea { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; id: messageDraft; objectName: "teamMessageDraft"; Layout.fillWidth: true; Layout.preferredHeight: 80; text: teams.messageDraft; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; selectByMouse: true; placeholderText: qsTr("Messaggio al team…"); onTextChanged: if (activeFocus) teams.setMessageDraft(text); Accessible.name: qsTr("Messaggio al team"); Keys.onPressed: function(event) { if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && (event.modifiers & Qt.ControlModifier)) { teams.sendMessage(String(recipient.currentValue || "all")); event.accepted = true } } }
                    KButton { objectName: "sendTeamMessageButton"; text: teams.messagePending ? qsTr("In attesa…") : qsTr("Invia"); primary: true; enabled: workspace.connected && teams.selectedId.length > 0 && !teams.messagePending && messageDraft.text.trim().length > 0; onClicked: teams.sendMessage(String(recipient.currentValue || "all")) }
                }
            }
            ScrollView {
                id: resultScroll; clip: true; contentWidth: availableWidth
                ColumnLayout {
                    width: resultScroll.availableWidth; spacing: 14
                    ReadText { objectName: "teamOutcome"; text: teams.outcomeText || qsTr("I risultati appariranno dopo gli esiti registrati dal servizio."); Layout.fillWidth: true }
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; text: qsTr("ATTIVITÀ DEL TEAM"); textFormat: Text.PlainText; color: accent; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3) }
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; text: qsTr("Piani, stati e messaggi. I dettagli degli strumenti e dello streaming si aprono da ciascuna esecuzione."); textFormat: Text.PlainText; color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2); wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Repeater {
                        model: teamActivityModel
                        delegate: ColumnLayout {
                            required property string title
                            required property string body
                            required property string status
                            Layout.fillWidth: true
                            Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; text: title + " · " + status; textFormat: Text.PlainText; color: muted; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
                            ReadText { color: appearance.colors.codeText; text: body; Layout.fillWidth: true; font.pixelSize: appearance.codeFont.pixelSize - 1; font.family: appearance.codeFont.family; font.styleName: appearance.codeFont.styleName; font.weight: appearance.codeFont.weight; font.italic: appearance.codeFont.italic }
                        }
                    }
                }
            }
            ScrollView {
                id: approvalScroll; clip: true; contentWidth: availableWidth
                ColumnLayout {
                    width: approvalScroll.availableWidth; spacing: 12
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; text: teams.approvals.length ? qsTr("Ogni decisione riguarda l'agente e l'esecuzione indicati.") : qsTr("Nessuna autorizzazione in attesa."); textFormat: Text.PlainText; color: muted; Layout.fillWidth: true; wrapMode: Text.Wrap }
                    Repeater {
                        model: teamApprovalsModel
                        delegate: Rectangle {
                            required property var record
                            Layout.fillWidth: true; implicitHeight: approvalColumn.implicitHeight + 24
                            radius: 8; color: appearance.colors.surfaceAlt; border.color: appearance.colors.warning
                            ColumnLayout {
                                id: approvalColumn; anchors.fill: parent; anchors.margins: 12
                                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; text: localization.format(qsTr("%1 · esecuzione %2"), [record.member_id, record.run_id]); textFormat: Text.PlainText; color: appearance.colors.text; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                ReadText { color: appearance.colors.codeText; text: record.description; Layout.fillWidth: true; font.family: appearance.codeFont.family; font.styleName: appearance.codeFont.styleName; font.weight: appearance.codeFont.weight; font.italic: appearance.codeFont.italic; font.pixelSize: appearance.codeFont.pixelSize - 1 }
                                KButton { text: qsTr("Dettagli esecuzione"); subtle: true; onClicked: teams.openRunEvidence(record.run_id) }
                                RowLayout {
                                    KButton { text: qsTr("Rifiuta"); enabled: workspace.connected && !teams.approvalPending; onClicked: teams.approveRequest(record.run_id, record.approval_id, "decline") }
                                    KButton { text: qsTr("Annulla richiesta"); enabled: workspace.connected && !teams.approvalPending; onClicked: teams.approveRequest(record.run_id, record.approval_id, "cancel") }
                                    KButton { objectName: "teamApprove_" + record.run_id; text: qsTr("Consenti"); primary: true; enabled: workspace.connected && !teams.approvalPending && !((record.request || {}).truncated); onClicked: teams.approveRequest(record.run_id, record.approval_id, "accept") }
                                }
                                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; text: qsTr("Richiesta troppo estesa per questa vista. Puoi rifiutarla o annullarla."); textFormat: Text.PlainText; color: muted; visible: Boolean((record.request || {}).truncated); Layout.fillWidth: true; wrapMode: Text.Wrap }
                            }
                        }
                    }
                }
            }
        }
        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; text: teams.historyNotice; textFormat: Text.PlainText; visible: text.length > 0; color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); wrapMode: Text.Wrap; Layout.fillWidth: true }
        ColumnLayout {
            objectName: "teamContinuationPanel"
            visible: ["completed", "failed", "cancelled", "interrupted", "paused"].indexOf(teams.team.state) >= 0
            Layout.fillWidth: true; spacing: 6
            Rectangle { Layout.fillWidth: true; implicitHeight: 1; Layout.bottomMargin: 5; color: appearance.colors.border }
            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: qsTr("Round alla ripresa · 0 = illimitati")
                    Layout.fillWidth: true; wrapMode: Text.Wrap
                    font: appearance.uiFont; color: appearance.colors.text; textFormat: Text.PlainText
                }
                SpinBox {
                    objectName: "teamContinuationRoundLimitInput"
                    font: appearance.uiFont; editable: true; from: 0; to: 10000
                    value: teams.continuationRoundLimit
                    enabled: workspace.connected && teams.canEditContinuation
                    onValueModified: teams.setContinuationRoundLimit(value)
                    Accessible.name: qsTr("Round alla ripresa · 0 = illimitati")
                }
            }
            Text {
                text: teams.continuationOutcomeUncertain ? qsTr("Ripresa con esito da verificare: riprova con lo stesso limite.") : qsTr("Il limite si applica alla nuova continuazione; i risultati precedenti restano conservati.")
                Layout.fillWidth: true; wrapMode: Text.Wrap
                font: appearance.uiFont; color: muted; textFormat: Text.PlainText
            }
            RowLayout {
                Layout.fillWidth: true
                TextField { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; objectName: "teamContinuationInput"; Layout.fillWidth: true; enabled: workspace.connected && teams.canEditContinuation; text: teams.continuationDraft; placeholderText: qsTr("Istruzione per una nuova continuazione…"); selectByMouse: true; onTextEdited: teams.setContinuationDraft(text); Accessible.name: qsTr("Istruzione per continuare il team") }
                KButton { objectName: "resumeTeamButton"; text: qsTr("Riprendi"); primary: true; enabled: workspace.connected && teams.canResume; onClicked: teams.resumeTeam() }
            }
        }
    }
}
