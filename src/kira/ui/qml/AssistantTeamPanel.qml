import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: panel
    objectName: "assistantTeamPanel"
    property bool expanded: false
    property bool compactCompleted: false
    property bool dockDetails: false
    onExpandedChanged: {
        if (!dockDetails) return
        if (expanded && !teamDetails.visible) teamDetails.open()
        else if (!expanded && teamDetails.visible) teamDetails.close()
    }
    property string draftContext: ""
    signal memoryRequested()
    signal researchRequested()
    function syncMessageContext() {
        var current = assistant.current
        var context = (workspace.project.id || "") + ":" + workspace.sessionId + ":" + (current.delegation_id || "")
        if (context !== draftContext) {
            draftContext = context
            teamMessage.text = assistant.messageDraft
        }
    }
    Component.onCompleted: syncMessageContext()
    readonly property var current: assistant.current.team_id && assistant.currentTeamId && assistant.current.team_id !== assistant.currentTeamId ? ({}) : assistant.current
    readonly property bool hasDelegation: !!current.delegation_id
    readonly property bool hasTeamContext: hasDelegation || assistant.currentTeamId.length > 0
    readonly property bool completedCollapsed: compactCompleted && current.state === "completed" && !current.completion_warning && !expanded && !assistant.error.length && !current.error
    implicitHeight: dockDetails ? (inlineNotice.visible ? inlineNotice.implicitHeight + 8 : 0) : (hasDelegation || assistant.error.length) && !completedCollapsed ? card.implicitHeight + 8 : 0
    function toggleDetails() { expanded = !expanded; teamMessage.text = assistant.messageDraft }
    function showEnvironment() {
        environmentDialog.open()
        assistant.refresh()
    }
    Connections {
        target: assistant
        function onMessageAccepted() { teamMessage.text = assistant.messageDraft }
        function onChanged() { Qt.callLater(panel.syncMessageContext) }
    }
    Connections {
        target: workspace
        function onChatChanged() { panel.expanded = false; teamMessage.text = assistant.messageDraft }
    }
    RowLayout {
        id: inlineNotice
        objectName: "assistantInlineNotice"
        visible: panel.dockDetails && !teamDetails.visible && noticeText.text.length > 0
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        spacing: 8
        Rectangle { width: 2; Layout.fillHeight: true; color: panel.current.completion_warning || assistant.error.length ? appearance.colors.warning : appearance.colors.accent }
        Text {
            id: noticeText
            text: assistant.error || panel.current.error || panel.current.completion_warning
                || assistant.followupError || (assistant.followupPending ? assistant.followupLabel : "")
                || (panel.current.state === "pending" ? panel.current.handoff_text || qsTr("Il team partirà dopo la conclusione del turno corrente.")
                    : panel.current.state === "finishing" ? qsTr("Il team sta terminando gli incarichi e raccogliendo i risultati.") : "")
            Layout.fillWidth: true; Layout.minimumWidth: 0
            wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
            font: appearance.uiFont; color: panel.current.completion_warning || assistant.error.length ? appearance.colors.warning : appearance.colors.muted
            textFormat: Text.PlainText
        }
        KButton { text: qsTr("Apri Team"); subtle: true; implicitHeight: 30; onClicked: panel.toggleDetails(); toolTipText: qsTr("Stato, incarichi e risultati del team") }
    }
    KiraPanel {
        id: teamDetails; objectName: "assistantTeamDetails"
        title: qsTr("Team del progetto")
        onOpened: panel.expanded = true
        onClosed: panel.expanded = false
        contentItem: ScrollView {
            id: detailsScroll
            clip: true; contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ScrollBar.vertical: KScrollBar {}
            Item { id: dockedContent; width: detailsScroll.availableWidth; implicitHeight: card.implicitHeight }
        }
    }
    Rectangle {
        id: card
        parent: panel.dockDetails ? dockedContent : panel
        visible: (panel.hasTeamContext || assistant.error.length > 0) && (panel.dockDetails || !panel.completedCollapsed)
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        implicitHeight: content.implicitHeight + 16
        height: implicitHeight
        radius: 10; color: panel.dockDetails ? "transparent" : appearance.colors.surface; border.width: 0
        ColumnLayout {
            id: content
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.margins: panel.dockDetails ? 0 : 8; spacing: 12
            Flow {
                Layout.fillWidth: true
                spacing: 4
                Text {
                    objectName: "assistantTeamStatus"
                    text: panel.hasDelegation ? qsTr("Team · %1").arg(panel.current.label) : panel.hasTeamContext ? qsTr("Team del progetto") : qsTr("Assistente Kira")
                    font: appearance.uiFont; color: appearance.colors.accent
                    width: Math.min(implicitWidth + 8, parent.width)
                    height: 34 + Math.max(0, appearance.uiFont.pixelSize - 13)
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight; textFormat: Text.PlainText
                }
                KButton {
                    objectName: "assistantTeamExpand"
                    text: panel.expanded ? qsTr("Nascondi") : qsTr("Dettagli")
                    implicitHeight: 34 + Math.max(0, appearance.uiFont.pixelSize - 13)
                    subtle: true; visible: panel.hasDelegation && !panel.dockDetails
                    onClicked: panel.toggleDetails()
                }
                KButton {
                    objectName: "assistantOpenTeam"
                    text: qsTr("Apri Team"); visible: assistant.currentTeamId.length > 0
                    implicitHeight: 34 + Math.max(0, appearance.uiFont.pixelSize - 13)
                    onClicked: assistant.openTeam(assistant.currentTeamId)
                }
                KButton {
                    objectName: "assistantCancelTeam"
                    text: qsTr("Ferma team"); visible: panel.hasDelegation && assistant.canCancel
                    implicitHeight: 34 + Math.max(0, appearance.uiFont.pixelSize - 13)
                    enabled: !assistant.commandPending
                    onClicked: assistant.cancel()
                }
                KButton {
                    objectName: "assistantResumeTeam"
                    text: panel.current.state === "completed" ? qsTr("Continua team") : qsTr("Riprendi team"); visible: panel.hasDelegation && assistant.canResume
                    implicitHeight: 34 + Math.max(0, appearance.uiFont.pixelSize - 13)
                    enabled: !assistant.commandPending
                    onClicked: assistant.resume()
                }
                KButton {
                    objectName: "assistantConfigureAnotherTeam"
                    text: qsTr("Configura team"); subtle: true; visible: panel.expanded
                    implicitHeight: 34 + Math.max(0, appearance.uiFont.pixelSize - 13)
                    onClicked: assistant.configureTeam()
                }
            }
            Text {
                objectName: "assistantPendingHandoff"
                visible: panel.current.state === "pending"
                text: panel.current.handoff_text || qsTr("Il team partirà dopo la conclusione del turno corrente.")
                Layout.fillWidth: true; wrapMode: Text.Wrap; font: appearance.uiFont
                color: appearance.colors.muted; textFormat: Text.PlainText
            }
            Text {
                objectName: "assistantCompletionWarning"
                text: panel.current.completion_warning || ""; visible: text.length > 0
                Layout.fillWidth: true; wrapMode: Text.Wrap; font: appearance.uiFont
                color: appearance.colors.warning; textFormat: Text.PlainText
            }
            Text {
                visible: panel.current.state === "finishing"
                text: qsTr("Il team sta terminando gli incarichi e raccogliendo i risultati.")
                Layout.fillWidth: true; wrapMode: Text.Wrap; font: appearance.uiFont
                color: appearance.colors.muted; textFormat: Text.PlainText
            }
            Text {
                objectName: "assistantFollowupState"
                text: assistant.followupLabel
                visible: text.length > 0
                Layout.fillWidth: true; wrapMode: Text.Wrap; font: appearance.uiFont
                color: appearance.colors.accent; textFormat: Text.PlainText
            }
            Text {
                objectName: "assistantFollowupError"
                text: assistant.followupError
                visible: text.length > 0
                Layout.fillWidth: true; wrapMode: Text.Wrap; font: appearance.uiFont
                color: appearance.colors.warning; textFormat: Text.PlainText
            }
            ColumnLayout {
                visible: panel.expanded && panel.hasDelegation
                Layout.fillWidth: true; spacing: 8
                Text {
                    objectName: "assistantTeamGoal"
                    text: panel.current.goal || ""
                    Layout.fillWidth: true; wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight
                    font: appearance.uiFont; color: appearance.colors.text; textFormat: Text.PlainText
                }
                Text {
                    objectName: "assistantTeamProgress"
                    text: (panel.current.members_count === 1 ? qsTr("%1 membro · %2/%3 incarichi completati") : qsTr("%1 membri · %2/%3 incarichi completati")).arg(panel.current.members_count || 0).arg(panel.current.completed_steps || 0).arg(panel.current.total_steps || 0)
                    Layout.fillWidth: true; wrapMode: Text.Wrap
                    font: appearance.uiFont; color: appearance.colors.muted; textFormat: Text.PlainText
                }
                ReadText {
                    text: panel.current.summary || panel.current.error || ""
                    visible: text.length > 0; Layout.fillWidth: true; Layout.maximumHeight: 100; clip: true
                    font: appearance.contentFont; color: appearance.colors.text
                }
                Text {
                    visible: ["completed", "failed", "cancelled", "interrupted", "paused"].indexOf(panel.current.state) >= 0
                    text: assistant.canResume ? qsTr("La ripresa richiede un tuo comando esplicito.") : panel.current.resume_unavailable_reason || qsTr("Ripresa non disponibile: apri Team per verificare lo stato degli incarichi.")
                    Layout.fillWidth: true; wrapMode: Text.Wrap
                    font: appearance.uiFont; color: appearance.colors.muted; textFormat: Text.PlainText
                }
                RowLayout {
                    visible: ["completed", "failed", "cancelled", "interrupted", "paused"].indexOf(panel.current.state) >= 0
                    Layout.fillWidth: true
                    Text {
                        text: qsTr("Round alla ripresa · 0 = illimitati")
                        Layout.fillWidth: true; wrapMode: Text.Wrap
                        font: appearance.uiFont; color: appearance.colors.text; textFormat: Text.PlainText
                    }
                    SpinBox {
                        objectName: "assistantResumeRoundLimitInput"
                        font: appearance.uiFont; editable: true; from: 0; to: 10000
                        value: assistant.resumeRoundLimit
                        enabled: assistant.canEditResumeRoundLimit
                        onValueModified: assistant.setResumeRoundLimit(value)
                        Accessible.name: qsTr("Round alla ripresa · 0 = illimitati")
                    }
                }
                Text {
                    visible: ["completed", "failed", "cancelled", "interrupted", "paused"].indexOf(panel.current.state) >= 0
                    text: assistant.resumeOutcomeUncertain ? qsTr("Ripresa con esito da verificare: riprova con lo stesso limite.") : qsTr("Il limite si applica alla nuova continuazione; i risultati precedenti restano conservati.")
                    Layout.fillWidth: true; wrapMode: Text.Wrap
                    font: appearance.uiFont; color: appearance.colors.muted; textFormat: Text.PlainText
                }
                RowLayout {
                    visible: assistant.canMessage; Layout.fillWidth: true
                    TextField {
                        font: appearance.uiFont; color: appearance.colors.text
                        maximumLength: 8000
                        background: Rectangle { color: appearance.colors.background; border.color: appearance.colors.border; radius: 8 }
                        id: teamMessage; objectName: "assistantTeamMessage"
                        Layout.fillWidth: true; placeholderText: qsTr("Indicazioni al coordinatore…")
                        text: assistant.messageDraft
                        onTextEdited: assistant.setMessageDraft(text)
                        onAccepted: if (text.trim().length > 0) assistant.sendMessage("coordinator", text)
                        Accessible.name: qsTr("Messaggio al coordinatore del team")
                    }
                    KButton {
                        objectName: "assistantSendMessage"
                        text: qsTr("Invia al team")
                        enabled: assistant.canMessage && teamMessage.text.trim().length > 0
                        onClicked: assistant.sendMessage("coordinator", teamMessage.text)
                    }
                }
                Text {
                    objectName: "assistantMessageDelivery"
                    visible: assistant.deliveryNotice.length > 0
                    text: assistant.deliveryNotice
                    Layout.fillWidth: true; wrapMode: Text.Wrap
                    font: appearance.uiFont; color: appearance.colors.accent; textFormat: Text.PlainText
                }
                Text {
                    visible: assistant.canMessage
                    text: qsTr("Le indicazioni arrivano nella casella del coordinatore. Non avviano un nuovo turno della chat.")
                    Layout.fillWidth: true; wrapMode: Text.Wrap
                    font: appearance.uiFont; color: appearance.colors.muted; textFormat: Text.PlainText
                }
            }
            Text {
                objectName: "assistantTeamError"
                text: assistant.error || panel.current.error || ""; visible: text.length > 0
                Layout.fillWidth: true; wrapMode: Text.Wrap; font: appearance.uiFont
                color: appearance.colors.danger; textFormat: Text.PlainText
            }
        }
    }
    KiraKnowledgeDialog {
        id: environmentDialog; objectName: "assistantEnvironmentDialog"; inspectorKey: "conoscenza"
        parent: Overlay.overlay
        onMemoryRequested: panel.memoryRequested()
        onResearchRequested: panel.researchRequested()
    }
}
