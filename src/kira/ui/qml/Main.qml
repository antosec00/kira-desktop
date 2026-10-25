import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic
    id: window
    objectName: "mainWindow"
    visible: true
    width: 1380
    height: 900
    minimumWidth: 880
    minimumHeight: 620
    title: workspace.project.name ? workspace.project.name + " — Kira Desktop" : "Kira Desktop"
    color: appearance.colors.background
    font.family: appearance.uiFont.family
    font.pixelSize: appearance.uiFont.pixelSize
    palette.window: appearance.colors.background
    palette.windowText: appearance.colors.text
    palette.brightText: appearance.colors.text
    palette.base: appearance.colors.surface
    palette.text: appearance.colors.text
    palette.button: appearance.colors.surfaceAlt
    palette.buttonText: appearance.colors.text
    palette.highlight: appearance.colors.accent
    palette.highlightedText: appearance.colors.accentText
    palette.placeholderText: appearance.colors.muted
    palette.mid: appearance.colors.border
    palette.dark: appearance.colors.sidebar
    palette.alternateBase: appearance.colors.surfaceAlt
    palette.light: appearance.colors.border
    palette.midlight: appearance.colors.border
    palette.shadow: appearance.colors.border
    palette.toolTipBase: appearance.colors.surfaceAlt
    palette.toolTipText: appearance.colors.text
    palette.link: appearance.colors.accent
    palette.disabled.text: appearance.colors.disabledText
    palette.disabled.buttonText: appearance.colors.disabledText
    readonly property bool compact: workspace.density === "compact" || window.height < 720
    property int page: 0
    property alias workspacePanels: panelHost
    onPageChanged: panelHost.closeCurrent()
    // "Apri le opzioni del modello con la chat": the stored preference opens the
    // model popover whenever a chat is shown; closing it never changes the preference.
    readonly property string modelOptionsScope: workspace.modelControlsVisible + "|" + page + "|" + hasProject + "|" + workspace.sessionId
    onModelOptionsScopeChanged: if (workspace.modelControlsVisible && page === 0 && hasProject && !workspace.busy) Qt.callLater(function() { modelPopover.open() })
    readonly property bool browserFocused: workspace.web.paneVisible && (workspace.web.expanded || window.width < 1120)
    readonly property bool studioFocused: workspace.studio.paneVisible && (workspace.studio.expanded || window.width < 1120)
    readonly property bool visualFocused: browserFocused || studioFocused
    readonly property bool motionEnabled: !appearance.reduceMotion && window.visible && window.visibility !== Window.Minimized
    readonly property bool chatMotionEnabled: motionEnabled && page === 0
    readonly property var chatIndicators: workspace.chatStatusIndicators
    readonly property bool browserPresented: window.visible && window.visibility !== Window.Minimized && page === 0
    onBrowserPresentedChanged: workspace.web.setWindowActive(browserPresented)
    readonly property bool detailsVisible: workspace.uiPreferences.details_visible
    readonly property bool detailsDrawerRequested: detailsVisible && width < 1120 && hasProject && page === 0 && !workspace.web.paneVisible && !workspace.studio.paneVisible
    onDetailsDrawerRequestedChanged: syncDetailsDrawer()
    Component.onCompleted: syncDetailsDrawer()
    function syncDetailsDrawer() {
        if (!detailsDrawer) return
        if (detailsDrawerRequested) detailsDrawer.open()
        else detailsDrawer.close()
    }
    readonly property int chatFontSize: appearance.contentFont.pixelSize
    readonly property string closePolicy: workspace.uiPreferences.confirm_close
    property bool closeConfirmed: false
    property bool followOutput: true
    property bool memoryExport: false
    property string eventDetails: ""
    property bool eventDetailsIsRun: false
    readonly property string eventDetailsTitle: eventDetailsIsRun ? qsTr("Dettagli dell'esecuzione") : qsTr("Dati registrati dello strumento")
    readonly property color muted: appearance.colors.muted
    readonly property color accent: appearance.colors.accent
    readonly property bool hasProject: Boolean(workspace.project.id)
    KFormat { id: kformat }

    // Tray tools are icons; their labels appear on request, their names always.
    component ChatToolButton: KButton {
        id: toolButton
        property string label: ""
        subtle: true
        text: workspace.toolLabels ? label : ""
        implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
        implicitWidth: text.length ? Math.max(38, toolLabel.advanceWidth + 44) : 34
        font.pixelSize: Math.max(11, appearance.uiFont.pixelSize - 1)
        toolTipText: label
        Accessible.name: label
        TextMetrics { id: toolLabel; text: toolButton.label; font: toolButton.font }
    }

    // Header actions keep their icon and show the label while the title has room.
    component HeaderSecondaryButton: KButton {
        id: headerButton
        property string label: ""
        property string compactText: ""
        property string compactIcon: ""
        readonly property real fullLabelWidth: Math.max(38, fullLabel.advanceWidth + 28 + (compactIcon ? 29 : 0))
        text: projectHeader.compactActions ? compactText : label
        iconName: compactIcon
        implicitWidth: projectHeader.compactActions ? 38 : fullLabelWidth
        implicitHeight: 38
        subtle: true
        Accessible.name: label
        toolTipText: label
        TextMetrics { id: fullLabel; text: headerButton.label; font: headerButton.font }
    }
    // Side panels share one tab strip; each panel declares its own key.
    readonly property var inspectorTabs: [
        { key: "ricerca", label: qsTr("Ricerca del progetto"), icon: "research" },
        { key: "anima", label: qsTr("Anima di Kira"), icon: "anima" },
        { key: "contesto", label: qsTr("Contesto e memoria della chat"), icon: "context" },
        { key: "memoria", label: qsTr("Memoria del progetto"), icon: "memory" },
        { key: "conoscenza", label: qsTr("Conoscenza e strumenti"), icon: "knowledge" },
        { key: "uso", label: qsTr("Uso e risorse"), icon: "usage" },
        { key: "file", label: qsTr("Artefatti del progetto"), icon: "file" },
        { key: "riprendi", label: qsTr("Riprendi lavoro"), icon: "resume" }
    ]
    function openInspector(key) {
        if (key === "ricerca") researchDialog.open()
        else if (key === "anima") soulDialog.open()
        else if (key === "contesto") chatContextDialog.open()
        else if (key === "memoria") sharedMemoryDialog.open()
        else if (key === "conoscenza") assistantTeamPanel.showEnvironment()
        else if (key === "uso") metricsDialog.open()
        else if (key === "file") artifactsDialog.open()
        else if (key === "riprendi") resumeDialog.open()
    }

    property bool restoringDraft: false
    property int commandStart: -1
    property int mentionStart: -1
    property string pickerContext: ""
    function openActions(query) { actionPalette.initialQuery = query || ""; actionPalette.open() }
    function openProjectFiles() {
        pickerContext = JSON.stringify([workspace.project.id || "", workspace.sessionId])
        projectFileDialog.initialQuery = ""
        projectFileDialog.open()
    }
    // A "/" typed in the composer opens the palette; only choosing an action
    // there consumes it. Esc and every other route leave the draft untouched.
    function chooseAction(action) {
        if (commandStart >= 0 && prompt.text.slice(commandStart, commandStart + 1) === "/") {
            prompt.remove(commandStart, commandStart + 1)
        }
        commandStart = -1
        performAction(action)
    }
    // A suggested export name: the project and what is exported, without path characters.
    function exportName(label) {
        return ((workspace.project.name || "Kira") + " - " + label).replace(/[\\/:*?"<>|]+/g, "-")
    }
    function performAction(action) {
        if (action === "new_project") newProject.open()
        else if (action === "new_chat") { window.page = 0; workspace.newChat(); prompt.forceActiveFocus() }
        else if (action === "files") { mentionStart = -1; openProjectFiles() }
        else if (["queue", "history", "archives", "changes", "goal"].indexOf(action) >= 0) workbenchDialog.openSection(action)
        else if (action === "chat_tools") workbenchDialog.openSection("chat")
        else if (action === "chat") { workbenchDialog.close(); window.page = 0 }
        else if (action === "context") chatContextDialog.open()
        else if (action === "compact") {
            // Direct providers compact from the context panel; a native compaction
            // is confirmed only when it can start. Otherwise the chat tools explain why.
            if (chatContext.directContext) chatContextDialog.open()
            else if (workbench.canCompact) { workbenchDialog.openSection("chat"); workbenchDialog.confirmCompact() }
            else workbenchDialog.openSection("chat")
        }
        else if (action === "export_chat") chatMarkdownExport.open()
        else if (action === "copy_last") workbench.copyLastOutput()
        else if (["execute", "plan", "review", "recap"].indexOf(action) >= 0) {
            workbench.setMode(action)
            window.page = 0
            if (action === "recap" && !prompt.text.trim()) prompt.text = qsTr("Riepiloga la conversazione, le decisioni prese e i prossimi passi.")
            if (action === "review" && !prompt.text.trim()) prompt.text = qsTr("Esamina le modifiche del progetto e segnala problemi concreti, senza modificare file.")
            prompt.forceActiveFocus()
        }
        else if (action === "model") { window.page = 0; modelPopover.open() }
        else if (action === "permissions") memoryDialog.open()
        else if (action === "memory") sharedMemoryDialog.open()
        else if (action === "team") window.page = 4
        else if (action === "usage") metricsDialog.open()
        else if (action === "settings") window.page = 3
        else if (action === "diagnostics") diagnosticsDialog.open()
        else if (action === "native") actionDialog.open()
    }
    Connections { target: assistant; function onOpenTeamRequested() { window.page = 4 } }
    Connections {
        target: knowledge
        function onInsertRequested(text) {
            window.page = 0
            prompt.insert(prompt.cursorPosition, (prompt.text.length ? "\n\n" : "") + text)
            prompt.forceActiveFocus()
        }
    }
    function submitComposer() {
        if (!sendButton.enabled) return
        workbench.submit(prompt.text)
    }

    onClosing: function(close) {
        if (!closeConfirmed && (closePolicy === "always" || (closePolicy === "active" && (workspace.busy || teams.busy || assistant.active)))) {
            close.accepted = false
            closeDialog.open()
        }
    }
    Shortcut { sequence: "Ctrl+Shift+Q"; enabled: hasProject; onActivated: inputDialog.open() }
    Shortcut { sequence: "Ctrl+Shift+P"; onActivated: { commandStart = -1; openActions("") } }
    Shortcut { sequence: "Ctrl+K"; onActivated: actionDialog.open() }
    Shortcut { sequence: "Ctrl+B"; onActivated: workspace.cycleSidebarMode() }
    Shortcut { sequence: "Ctrl+Shift+R"; onActivated: if (hasProject) resumeDialog.open() }
    Shortcut { sequence: "Ctrl+N"; onActivated: newProject.open() }
    Shortcut { sequence: "Ctrl+Shift+N"; enabled: hasProject; onActivated: window.performAction("new_chat") }
    Shortcut { sequence: "Ctrl+L"; onActivated: { window.page = 0; prompt.forceActiveFocus() } }
    Shortcut { sequence: "Ctrl+Shift+C"; onActivated: workspace.copyTranscript() }
    Connections {
        target: workspace
        function onPromptAccepted() { prompt.text = ""; window.followOutput = true }
        function onChatChanged() { panelHost.closeCurrent(); window.commandStart = -1; window.mentionStart = -1; window.restoringDraft = true; prompt.text = workspace.draftText; window.restoringDraft = false; window.followOutput = true }
        function onProjectCreated() { newProject.close(); window.page = 0 }
        function onTranscriptChanged() { conversation.scheduleFollow() }
        function onChatLoadingChanged() {
            conversation.historyReady = false
            if (!workspace.chatLoading) conversation.scheduleFollow()
        }
    }

    Connections {
        target: workbench
        function onShowPanel(section) { workbenchDialog.openSection(section) }
        function onInvokeAction(action) { window.performAction(action) }
        function onReplaceComposer(text) {
            window.page = 0
            prompt.insert(prompt.cursorPosition, (prompt.text.length ? "\n\n" : "") + text)
            prompt.forceActiveFocus()
        }
        function onInsertFile(text) {
            if (window.pickerContext !== JSON.stringify([workspace.project.id || "", workspace.sessionId])) return
            var position = prompt.cursorPosition
            if (window.mentionStart >= 0 && prompt.text.slice(window.mentionStart, window.mentionStart + 1) === "@") {
                position = window.mentionStart
                prompt.remove(position, position + 1)
            }
            prompt.insert(position, text + " ")
            window.mentionStart = -1
            prompt.forceActiveFocus()
        }
    }

    KiraPanelHost {
        id: panelHost
        x: workspaceColumn.x
        y: workspaceColumn.y + workspaceStack.y
        width: workspaceColumn.width
        height: workspaceStack.height
        onPanelPresented: {
            if (workspace.web.paneVisible) workspace.web.hideBrowser()
            if (workspace.studio.paneVisible) workspace.studio.hideStudio()
            if (detailsDrawer.visible) detailsDrawer.close()
        }
    }
    Connections {
        target: workspace.web
        function onChanged() {
            if (workspace.web.paneVisible) {
                workspace.studio.hideStudio()
                panelHost.closeCurrent()
            }
        }
    }
    Connections {
        target: workspace.studio
        function onChanged() { if (workspace.studio.paneVisible) panelHost.closeCurrent() }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0
        Rectangle {
            id: sidebar
            objectName: "sidebar"
            readonly property color sidebarInk: appearance.colors.sidebar
            // Whole, icon rail or hidden: the same controls, less chrome.
            readonly property bool rail: workspace.sidebarMode === "rail"
            visible: workspace.sidebarMode !== "hidden"
            Layout.fillHeight: true
            Layout.preferredWidth: rail ? 62 + Math.max(0, appearance.uiFont.pixelSize - 13) * 2
                : (window.width < 1080 ? 188 : 222) + Math.max(0, appearance.uiFont.pixelSize - 13) * 7
            color: Qt.rgba(sidebarInk.r, sidebarInk.g, sidebarInk.b, appearance.values.sidebar_translucent ? 0.76 : 1)
            ScrollView {
                id: sidebarScroll
                anchors.fill: parent
                padding: sidebar.rail ? 10 : 18
                clip: true
                contentWidth: availableWidth
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                id: sidebarColumn
                width: sidebarScroll.availableWidth
                height: Math.max(sidebarScroll.availableHeight, implicitHeight)
                spacing: window.compact ? 6 : 10
                // Short windows tighten the sidebar so every control stays in view.
                readonly property real navHeight: (window.compact ? 34 : 38) + Math.max(0, appearance.uiFont.pixelSize - 13)
                RowLayout {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    Layout.topMargin: 10
                    Layout.bottomMargin: window.compact ? 10 : 20
                    spacing: 11
                    Rectangle {
                        width: 44; height: 44; radius: 14
                        color: "transparent"
                        Image {
                            objectName: "kiraMascot"
                            anchors.fill: parent; anchors.margins: 3
                            source: "../assets/kira-mascot.png"
                            sourceSize.width: 128; sourceSize.height: 128
                            fillMode: Image.PreserveAspectFit
                            smooth: true; mipmap: true
                            Accessible.role: Accessible.Graphic
                            Accessible.name: qsTr("Kira, mascotte dell'applicazione")
                        }
                    }
                    ColumnLayout {
                        visible: !sidebar.rail
                        Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 0
                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: "kira"; color: appearance.colors.text; font.pixelSize: appearance.uiFont.pixelSize + 12; font.weight: Font.DemiBold; font.letterSpacing: 0.2 }
                        Text { objectName: "appVersionLabel"; Layout.fillWidth: true; Layout.minimumWidth: 0; fontSizeMode: Text.HorizontalFit; minimumPixelSize: 8; font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: "DESKTOP  /  " + workspace.appVersion; color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 4); font.letterSpacing: workspace.appVersion.length > 7 || window.width < 1080 ? 0 : 1.6 }
                    }
                }
                KButton { objectName: "actionPaletteButton"; text: sidebar.rail ? "" : qsTr("Cerca  ·  Ctrl+K"); iconName: "search"; Layout.fillWidth: true; implicitHeight: sidebarColumn.navHeight; subtle: true; leftAligned: !sidebar.rail; onClicked: actionDialog.open(); Accessible.name: qsTr("Cerca progetti e azioni, Control K"); toolTipText: sidebar.rail ? qsTr("Cerca  ·  Ctrl+K") : "" }
                Repeater {
                    model: [{title: qsTr("Progetti"), icon: "projects", page: 0}, {title: qsTr("Attività"), icon: "activity", page: 2}, {title: qsTr("Team"), icon: "team", page: 4}]
                    delegate: KButton {
                        required property int index
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        implicitHeight: (window.compact ? 36 : 43) + Math.max(0, appearance.uiFont.pixelSize - 13)
                        text: sidebar.rail ? "" : modelData.title
                        objectName: modelData.page === 4 ? "teamNavButton" : ""
                        enabled: modelData.page !== 4 || hasProject
                        iconName: modelData.icon
                        leftAligned: !sidebar.rail
                        checked: window.page === modelData.page; quietSelection: true
                        subtle: true
                        onClicked: window.page = modelData.page
                        Accessible.name: modelData.title
                        toolTipText: sidebar.rail ? modelData.title : ""
                    }
                }
                Rectangle { visible: !sidebar.rail; Layout.fillWidth: true; Layout.topMargin: window.compact ? 8 : 15; Layout.bottomMargin: window.compact ? 4 : 9; height: 1; color: appearance.colors.border }
                RowLayout {
                    visible: !sidebar.rail
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("PROGETTI E CHAT"); color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); font.letterSpacing: 1.1; Layout.fillWidth: true; Layout.minimumWidth: 0; wrapMode: Text.WordWrap }
                    KButton { text: "+"; subtle: true; implicitWidth: Math.ceil(28 * appearance.uiFont.pixelSize / 13); implicitHeight: 28 + Math.max(0, appearance.uiFont.pixelSize - 13); onClicked: newProject.open(); Accessible.name: qsTr("Nuovo progetto"); toolTipText: qsTr("Nuovo progetto · Ctrl+N") }
                }
                Item { visible: sidebar.rail; Layout.fillHeight: true }
                ListView {
                    id: projectList
                    objectName: "projectList"
                    visible: !sidebar.rail
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 60
                    clip: true
                    spacing: 5
                    model: workspace.projects
                    ScrollBar.vertical: ScrollBar {}
                    delegate: Column {
                        id: projectEntry
                        required property var modelData
                        property bool expanded: true
                        width: projectList.width
                        spacing: 3
                        KButton {
                            width: parent.width; implicitHeight: 38 + Math.max(0, appearance.uiFont.pixelSize - 13)
                            text: (workspace.project.id === projectEntry.modelData.id && projectEntry.expanded ? "▾  " : "▸  ") + projectEntry.modelData.name
                            leftAligned: true; subtle: true
                            font.weight: workspace.project.id === projectEntry.modelData.id ? Font.DemiBold : Font.Normal
                            toolTipText: projectEntry.modelData.root
                            onClicked: { if (workspace.project.id === projectEntry.modelData.id) projectEntry.expanded = !projectEntry.expanded; else { projectEntry.expanded = true; workspace.openProject(projectEntry.modelData.id) } window.page = 0; window.followOutput = true }
                        }
                        Column {
                            id: chatChildren
                            x: 12; width: parent.width - 12; spacing: 2
                            visible: workspace.project.id === projectEntry.modelData.id && projectEntry.expanded
                            Repeater {
                                model: chatChildren.visible ? workspace.sessions : []
                                delegate: RowLayout {
                                    id: chatEntry
                                    required property var modelData
                                    readonly property var activity: window.chatIndicators[modelData.id] || ({})
                                    readonly property bool indicatorVisible: !!activity.state
                                    readonly property real listY: projectEntry.y + chatChildren.y + y
                                    width: chatChildren.width; height: 36 + Math.max(0, appearance.uiFont.pixelSize - 13); spacing: 2
                                    KButton {
                                        id: chatSelect
                                        objectName: "chatSelect_" + chatEntry.modelData.id
                                        Layout.fillWidth: true; implicitHeight: 34 + Math.max(0, appearance.uiFont.pixelSize - 13)
                                        text: chatEntry.modelData.title || chatEntry.modelData.label || qsTr("Nuova chat")
                                        checked: workspace.sessionId === chatEntry.modelData.id
                                        quietSelection: true; selectionMark: true
                                        subtle: true; leftAligned: true
                                        toolTipText: text + " · " + kformat.providerLabel(chatEntry.modelData.backend) + " · " + chatEntry.modelData.model
                                            + (chatEntry.indicatorVisible ? " · " + chatEntry.activity.label : "")
                                        Accessible.name: qsTr("Apri chat %1").arg(text)
                                            + (chatEntry.indicatorVisible ? " · " + chatEntry.activity.label : "")
                                        contentItem: Item {
                                            Text {
                                                anchors.fill: parent
                                                leftPadding: 10; rightPadding: chatEntry.indicatorVisible ? 25 : 10
                                                text: chatSelect.text; font: chatSelect.font; color: chatSelect.foreground
                                                textFormat: Text.PlainText; elide: Text.ElideRight
                                                verticalAlignment: Text.AlignVCenter
                                            }
                                            StatusIndicator {
                                                objectName: "chatStatus_" + chatEntry.modelData.id
                                                anchors.right: parent.right; anchors.rightMargin: 7
                                                anchors.verticalCenter: parent.verticalCenter
                                                visible: chatEntry.indicatorVisible
                                                state: chatEntry.activity.state || "idle"
                                                diameter: 10
                                                animate: window.motionEnabled && chatEntry.visible
                                                    && chatEntry.listY + chatEntry.height > projectList.contentY
                                                    && chatEntry.listY < projectList.contentY + projectList.height
                                            }
                                        }
                                        onClicked: { workspace.selectSession(chatEntry.modelData.id); window.page = 0 }
                                    }
                                    KButton {
                                        objectName: "chatRename_" + chatEntry.modelData.id
                                        text: "⋯"; subtle: true; implicitWidth: Math.ceil(25 * appearance.uiFont.pixelSize / 13); implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                                        toolTipText: qsTr("Rinomina chat"); Accessible.name: qsTr("Rinomina chat")
                                        onClicked: { renameChatDialog.chatId = chatEntry.modelData.id; chatNameInput.text = chatEntry.modelData.title || ""; renameChatDialog.open() }
                                    }
                                }
                            }
                            KButton {
                                objectName: "newChatButton"; width: parent.width; implicitHeight: 34 + Math.max(0, appearance.uiFont.pixelSize - 13)
                                text: qsTr("+  Nuova chat"); subtle: true; leftAligned: true
                                checked: !workspace.sessionId && window.page === 0
                                onClicked: { workspace.newChat(); window.page = 0 }
                                toolTipText: qsTr("Nuova chat nel progetto · Ctrl+Shift+N")
                            }
                            KButton {
                                width: parent.width; implicitHeight: 32 + Math.max(0, appearance.uiFont.pixelSize - 13)
                                visible: workspace.sessionsHasMore; enabled: !workspace.sessionsLoading
                                text: qsTr("Altre chat…"); subtle: true; onClicked: workspace.loadMoreSessions()
                            }
                        }
                    }
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; anchors.top: parent.top; anchors.topMargin: 12; width: parent.width; visible: projectList.count === 0; text: qsTr("Un progetto conserva\ncontesto, file e risultati."); color: muted; lineHeight: 1.5; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1) }
                }
                KButton {
                    objectName: "assistantEnvironmentButton"
                    text: sidebar.rail ? "" : qsTr("Conoscenza e strumenti"); iconName: "knowledge"
                    Layout.fillWidth: true; implicitHeight: sidebar.rail ? 38 : Math.max(window.compact ? 40 : 46, appearance.uiFont.pixelSize * 2 + 16)
                    subtle: true; leftAligned: !sidebar.rail; wrapText: !sidebar.rail
                    enabled: hasProject && workspace.sessionId.length > 0
                    onClicked: { window.page = 0; assistantTeamPanel.showEnvironment() }
                    Accessible.name: qsTr("Conoscenza e strumenti")
                    toolTipText: qsTr("Conoscenza, capacità e strumenti di Kira")
                }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: window.compact ? 2 : 3
                    KButton { objectName: "modelsNavButton"; text: sidebar.rail ? "" : qsTr("Modelli"); iconName: "models"; leftAligned: !sidebar.rail; Layout.fillWidth: true; implicitHeight: sidebarColumn.navHeight; subtle: true; quietSelection: true; checked: window.page === 1; onClicked: window.page = 1; Accessible.name: qsTr("Modelli"); toolTipText: sidebar.rail ? qsTr("Modelli") : "" }
                    KButton { objectName: "settingsNavButton"; text: sidebar.rail ? "" : qsTr("Impostazioni"); iconName: "settings"; leftAligned: !sidebar.rail; Layout.fillWidth: true; implicitHeight: sidebarColumn.navHeight; subtle: true; quietSelection: true; checked: window.page === 3; onClicked: window.page = 3; Accessible.name: qsTr("Impostazioni"); toolTipText: sidebar.rail ? qsTr("Impostazioni") : "" }
                }
                KButton {
                    objectName: "updateChip"
                    visible: updates.available
                    text: sidebar.rail ? "" : qsTr("Aggiorna a %1").arg(updates.latestVersion)
                    iconName: "update"; Layout.fillWidth: true; implicitHeight: sidebarColumn.navHeight
                    subtle: true; quietSelection: true; checked: true; leftAligned: !sidebar.rail
                    onClicked: updateDialog.open()
                    Accessible.name: qsTr("Aggiornamento disponibile · %1").arg(updates.latestVersion)
                    toolTipText: sidebar.rail ? qsTr("Aggiornamento disponibile · %1").arg(updates.latestVersion) : ""
                }
                KButton { objectName: "onboardingNavButton"; text: sidebar.rail ? "" : qsTr("Guida iniziale"); iconName: "help"; Layout.fillWidth: true; implicitHeight: sidebarColumn.navHeight; subtle: true; leftAligned: !sidebar.rail; onClicked: onboardingDialog.open(); Accessible.name: qsTr("Guida iniziale"); toolTipText: sidebar.rail ? qsTr("Guida iniziale") : "" }
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: sidebar.rail ? 26 : connectionLabel.implicitHeight + (window.compact ? 16 : 26)
                    color: "transparent"
                    RowLayout {
                        anchors.fill: parent; anchors.margins: sidebar.rail ? 6 : 12; anchors.topMargin: sidebar.rail ? 6 : (window.compact ? 7 : 12); anchors.bottomMargin: sidebar.rail ? 6 : (window.compact ? 7 : 12); spacing: 8
                        Rectangle { objectName: "connectionDot"; width: 7; height: 7; radius: 3.5; color: workspace.connected ? appearance.colors.success : appearance.colors.warning; Layout.alignment: sidebar.rail ? Qt.AlignHCenter : Qt.AlignTop; Layout.topMargin: sidebar.rail ? 0 : 5; Accessible.name: workspace.connectionText
                            KToolTip { visible: connectionHover.containsMouse && sidebar.rail; text: workspace.connectionText }
                            MouseArea { id: connectionHover; anchors.fill: parent; anchors.margins: -6; hoverEnabled: true; acceptedButtons: Qt.NoButton } }
                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; id: connectionLabel; visible: !sidebar.rail; text: workspace.connectionText; color: muted; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
                    }
                }
            }
            }
        }
        Rectangle { visible: workspace.sidebarMode !== "hidden"; Layout.fillHeight: true; width: 1; color: appearance.colors.border }
        ColumnLayout {
            id: workspaceColumn
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0
            Rectangle {
                id: projectHeader
                objectName: "projectHeader"
                // One bar: where you are on the left, what you can do on the right.
                // Actions drop their labels before they crowd the title; the
                // threshold uses full labels so changing presentation cannot loop.
                readonly property bool compactActions: width - 70 < headerActions.fullWidth + 400
                Layout.fillWidth: true
                implicitHeight: (window.compact ? 52 : 58) + Math.max(0, appearance.uiFont.pixelSize - 13)
                color: appearance.colors.background
                Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: appearance.colors.border }
                RowLayout {
                    id: headerLayout
                    anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 14
                    spacing: 8
                    KButton {
                        objectName: "sidebarToggle"; iconName: "sidebar"; subtle: true; quietSelection: true
                        implicitWidth: 38; implicitHeight: 38
                        checked: workspace.sidebarMode !== "full"
                        onClicked: workspace.cycleSidebarMode()
                        toolTipText: qsTr("Barra laterale: intera, a icone o nascosta · Ctrl+B")
                        Accessible.name: workspace.sidebarMode === "full" ? qsTr("Riduci la barra laterale a icone") : workspace.sidebarMode === "rail" ? qsTr("Nascondi la barra laterale") : qsTr("Mostra la barra laterale")
                    }
                    RowLayout {
                        Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 7
                        Text {
                            id: headerKicker; objectName: "projectHeaderKicker"
                            font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText
                            text: window.page === 0 ? (workspace.project.name || "") : ["", qsTr("Inferenza e agenti"), qsTr("Evidenza durabile"), qsTr("Preferenze"), qsTr("Collaborazione")][window.page]
                            // The chat title keeps its room; the project name yields first.
                            visible: text.length > 0 && !projectHeader.compactActions
                            color: muted; font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 1)
                            elide: Text.ElideRight; Layout.maximumWidth: Math.max(80, headerLayout.width * 0.26); Layout.minimumWidth: 0
                        }
                        Text { font.family: appearance.uiFont.family; textFormat: Text.PlainText; text: "›"; color: muted; visible: headerKicker.visible; font.pixelSize: appearance.uiFont.pixelSize + 1 }
                        Text { objectName: "projectHeaderTitle"; font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: window.page === 0 ? (hasProject ? workspace.sessionTitle : qsTr("Il tuo prossimo progetto")) : ["", qsTr("Modelli"), qsTr("Attività"), qsTr("Impostazioni"), qsTr("Team del progetto")][window.page]; color: appearance.colors.text; font.pixelSize: appearance.uiFont.pixelSize + 3; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true; Layout.minimumWidth: Math.min(150, implicitWidth) }
                    }
                    Row {
                        id: headerActions
                        objectName: "projectHeaderActions"
                        spacing: 2
                        readonly property real fullWidth: {
                            let result = 0
                            for (let i = 0; i < children.length; ++i) {
                                let action = children[i]
                                if (action.visible)
                                    result += (action.fullLabelWidth || action.implicitWidth) + (result ? spacing : 0)
                            }
                            return result
                        }
                        Layout.minimumWidth: implicitWidth
                        Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
                        HeaderSecondaryButton {
                            objectName: "projectResearchButton"; label: qsTr("Ricerca"); compactIcon: "research"
                            visible: hasProject && (window.page === 0 || window.page === 4)
                            enabled: projectResearch.available
                            badge: projectResearch.pendingProposals
                            checked: panelHost.activePanel === researchDialog; quietSelection: true
                            toolTipText: projectResearch.pendingProposals > 0 ? qsTr("Ricerca del progetto · proposte dell’assistente da esaminare: %1").arg(projectResearch.pendingProposalsText) : qsTr("Ricerca del progetto")
                            Accessible.name: toolTipText
                            onClicked: researchDialog.open()
                        }
                        HeaderSecondaryButton {
                            objectName: "resumeWorkspaceButton"; label: qsTr("Riprendi"); compactIcon: "resume"
                            visible: hasProject && window.page === 0
                            checked: panelHost.activePanel === resumeDialog; quietSelection: true
                            toolTipText: qsTr("Riprendi lavoro · esiti, strumenti e prove · Ctrl+Shift+R"); Accessible.name: toolTipText
                            onClicked: resumeDialog.open()
                        }
                        HeaderSecondaryButton { objectName: "quickActionsButton"; label: qsTr("Azioni"); compactIcon: "actions"; toolTipText: qsTr("Azioni rapide · Ctrl+Shift+P"); Accessible.name: toolTipText; onClicked: { window.commandStart = -1; window.openActions("") } }
                        HeaderSecondaryButton { objectName: "projectExportButton"; label: qsTr("Esporta"); compactIcon: "export"; visible: hasProject && window.page === 0; toolTipText: qsTr("Esporta chat in Markdown"); Accessible.name: toolTipText; onClicked: { window.memoryExport = false; exportDialog.open() } }
                        HeaderSecondaryButton {
                            objectName: "detailsToggle"; label: qsTr("Dettagli"); compactIcon: "details"
                            visible: hasProject && window.page === 0
                            checked: window.width >= 1120 ? window.detailsVisible : detailsDrawer.visible; quietSelection: true
                            onClicked: workspace.setUiPreference("details_visible", !window.detailsVisible)
                            toolTipText: checked ? qsTr("Nascondi dettagli del progetto") : qsTr("Mostra dettagli del progetto")
                            Accessible.name: toolTipText
                        }
                        HeaderSecondaryButton { objectName: "headerNewProjectButton"; label: qsTr("Progetto"); compactIcon: "plus"; subtle: false; primary: true; toolTipText: qsTr("Nuovo progetto · Ctrl+N"); Accessible.name: qsTr("Nuovo progetto"); onClicked: newProject.open() }
                    }
                    Rectangle { width: 1; height: 22; color: appearance.colors.border }
                    KButton {
                        objectName: "themeToggle"; iconName: appearance.dark ? "sun" : "moon"; subtle: true
                        implicitWidth: 38; implicitHeight: 38
                        onClicked: appearance.setOption("mode", appearance.dark ? "light" : "dark")
                        toolTipText: appearance.dark ? qsTr("Passa al tema chiaro") : qsTr("Passa al tema scuro")
                        Accessible.name: toolTipText
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true
                visible: workspace.notice.length > 0
                implicitHeight: noticeText.implicitHeight + 24
                color: appearance.colors.surfaceAlt
                RowLayout {
                    anchors.fill: parent; anchors.margins: 10; anchors.leftMargin: 26
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; id: noticeText; Layout.fillWidth: true; text: workspace.notice; color: appearance.colors.warning; wrapMode: Text.Wrap; textFormat: Text.PlainText; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1) }
                    KButton { objectName: "dismissNoticeButton"; text: "×"; subtle: true; implicitWidth: Math.ceil(26 * appearance.uiFont.pixelSize / 13); implicitHeight: 26 + Math.max(0, appearance.uiFont.pixelSize - 13); toolTipText: qsTr("Chiudi avviso"); Accessible.name: toolTipText; onClicked: workspace.dismissNotice() }
                }
            }
            Rectangle {
                objectName: "inputBanner"
                Layout.fillWidth: true
                visible: inputs.pendingCount > 0
                implicitHeight: inputBannerRow.implicitHeight + 16
                color: appearance.colors.surfaceAlt
                RowLayout {
                    id: inputBannerRow
                    anchors.fill: parent; anchors.margins: 8; anchors.leftMargin: 24; anchors.rightMargin: 24
                    Text {
                        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic
                        Layout.fillWidth: true
                        text: (inputs.pendingCount === 1 ? qsTr("%1 richiesta in attesa di risposta") : qsTr("%1 richieste in attesa di risposta")).arg(inputs.pendingCount)
                        color: appearance.colors.text; wrapMode: Text.Wrap; textFormat: Text.PlainText; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1)
                    }
                    KButton { objectName: "inputPendingOpen"; text: qsTr("Domande"); primary: true; onClicked: inputDialog.open(); toolTipText: qsTr("Apri le domande · Ctrl+Shift+Q") }
                }
            }
            Rectangle {
                objectName: "browserOtherChatLogin"
                visible: workspace.approval.kind === "browser_manual_login" && !workspace.web.manualApproval
                Layout.fillWidth: true; implicitHeight: browserOtherLoginRow.implicitHeight + 16
                color: appearance.colors.surfaceAlt
                RowLayout {
                    id: browserOtherLoginRow; anchors.fill: parent; anchors.margins: 8
                    ReadText { Layout.fillWidth: true; text: qsTr("Un’altra chat attende il tuo accesso nel browser.") }
                    KButton { text: qsTr("Apri chat"); onClicked: { window.page = 0; workspace.web.showManualApproval() } }
                    KButton { text: qsTr("Annulla"); enabled: !workspace.approvalBusy; onClicked: workspace.approve("decline") }
                }
            }
            StackLayout {
                id: workspaceStack
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: window.page
                Item {
                    ColumnLayout {
                        anchors.centerIn: parent
                        width: Math.min(parent.width - 70, 540)
                        spacing: 18
                        visible: !hasProject
                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Uno spazio per lavorare.\nUn filo da ritrovare."); color: appearance.colors.text; font.pixelSize: appearance.uiFont.pixelSize + (window.width < 1000 ? 16 : 20); font.weight: Font.Medium; lineHeight: 1.2; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Scegli una cartella, collega un modello e porta avanti il lavoro. Conversazioni, strumenti e risultati restano nel progetto, anche quando chiudi questa finestra."); color: muted; font.pixelSize: appearance.uiFont.pixelSize + 2; lineHeight: 1.45; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        KButton { text: qsTr("Crea il primo progetto  →"); primary: true; implicitHeight: 44 + Math.max(0, appearance.uiFont.pixelSize - 13); Layout.topMargin: 12; onClicked: newProject.open() }
                        KButton { text: qsTr("Come funziona Kira"); subtle: true; visible: !workspace.onboardingComplete; onClicked: onboardingDialog.open() }
                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Kira Desktop organizza progetti, chat e risultati. Il provider scelto esegue i compiti AI."); visible: !workspace.onboardingComplete; color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1); Layout.fillWidth: true; wrapMode: Text.Wrap }
                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Modelli locali o in cloud  ·  Dati sul tuo computer"); color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2); Layout.topMargin: 14; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    }
                    SplitView {
                        id: browserSplit; objectName: "browserSplit"
                        anchors.fill: parent; visible: hasProject; orientation: Qt.Horizontal
                        handle: Rectangle { implicitWidth: 6; color: SplitHandle.pressed ? appearance.colors.accent : appearance.colors.border }
                        ColumnLayout {
                            id: chatColumn; objectName: "chatColumn"
                            visible: !window.visualFocused
                            SplitView.fillWidth: true; SplitView.minimumWidth: 430
                            spacing: 0
                            Rectangle {
                                objectName: "resumeHint"
                                visible: workspace.resumeHintVisible
                                Layout.fillWidth: true; implicitHeight: resumeBar.implicitHeight + 14; color: appearance.colors.surface
                                Layout.leftMargin: 24; Layout.rightMargin: 24; Layout.topMargin: 10; Layout.bottomMargin: 6
                                radius: 10; border.color: appearance.colors.border
                                RowLayout {
                                    id: resumeBar; anchors.fill: parent; anchors.margins: 7; anchors.leftMargin: 12; anchors.rightMargin: 8; spacing: 10
                                    ColumnLayout {
                                        Layout.fillWidth: true; spacing: 3
                                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Il prossimo passo"); color: accent; font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 1) }
                                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; text: qsTr("Ritrova esiti, promemoria e strumenti con «Riprendi»."); color: appearance.colors.muted; Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText; font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 2) }
                                    }
                                    KButton { objectName: "hideResumeHint"; text: qsTr("Nascondi"); subtle: true; implicitHeight: 30; onClicked: workspace.setUiPreference("resume_hint_visible", false); toolTipText: qsTr("Puoi mostrare di nuovo il suggerimento dalle Impostazioni.") }
                                }
                            }
                            Rectangle {
                                objectName: "branchContextBanner"; visible: workbench.parentSessionId.length > 0
                                Layout.fillWidth: true; Layout.leftMargin: 16; Layout.rightMargin: 16; Layout.bottomMargin: 8
                                implicitHeight: branchRow.implicitHeight + 16; color: appearance.colors.surfaceAlt; radius: 10
                                RowLayout {
                                    id: branchRow; anchors.fill: parent; anchors.margins: 8
                                    Text { text: qsTr("Diramazione di una chat esistente. Il contesto è ereditato; chat originale e file restano invariati."); font: appearance.uiFont; color: appearance.colors.muted; wrapMode: Text.Wrap; Layout.fillWidth: true; textFormat: Text.PlainText }
                                    KButton { objectName: "openParentChatButton"; text: qsTr("Apri chat originale"); onClicked: workspace.selectSession(workbench.parentSessionId) }
                                }
                            }
                            AssistantTeamPanel {
                                id: assistantTeamPanel
                                compactCompleted: true
                                dockDetails: true
                                onMemoryRequested: chatContextDialog.open()
                                onResearchRequested: researchDialog.open()
                                Layout.fillWidth: true; Layout.leftMargin: 24; Layout.rightMargin: 24
                                Layout.topMargin: implicitHeight > 0 ? 8 : 0
                            }
                            RowLayout {
                                objectName: "chatGoalAnchor"
                                visible: !!workbench.goal.objective
                                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28
                                Layout.topMargin: visible ? 10 : 0; Layout.bottomMargin: visible ? 6 : 0
                                KIcon { name: "goal"; ink: appearance.colors.accent; Layout.preferredWidth: 16; Layout.preferredHeight: 16 }
                                ColumnLayout {
                                    Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 2
                                    Text { objectName: "chatGoalStatus"; text: workbench.goalStatusLabel + (workbench.goalCycleLabel.length ? " · " + workbench.goalCycleLabel : ""); font: appearance.uiFont; color: appearance.colors.accent; textFormat: Text.PlainText; elide: Text.ElideRight; Layout.fillWidth: true }
                                    Text { objectName: "chatGoalText"; text: workbench.goalPreview; font: appearance.uiFont; color: muted; textFormat: Text.PlainText; elide: Text.ElideRight; maximumLineCount: 2; wrapMode: Text.Wrap; Layout.fillWidth: true; Layout.minimumWidth: 0 }
                                }
                                KButton { objectName: "chatGoalPause"; text: qsTr("Pausa"); visible: workbench.autonomousGoal.status === "active"; enabled: workspace.connected && !workbench.loading; subtle: true; implicitHeight: 28; onClicked: workbench.pauseGoal() }
                                KButton { text: qsTr("Apri"); subtle: true; implicitHeight: 28; onClicked: workbenchDialog.openSection("goal") }
                            }
                            Item {
                                Layout.fillWidth: true; Layout.fillHeight: true
                                ListView {
                                    id: conversation
                                    objectName: "conversation"
                                    anchors.fill: parent; anchors.margins: 23
                                    anchors.bottomMargin: 10
                                    // Visible delegates own their spacing so collapsed
                                    // group members do not accumulate empty gaps.
                                    clip: true; spacing: 0; model: transcriptModel
                                    cacheBuffer: 450
                                    reuseItems: true
                                    // Keep layout active while replay is staged, but reveal only
                                    // after the final rows have been positioned at the latest reply.
                                    opacity: historyReady && !workspace.chatLoading ? 1 : 0
                                    enabled: historyReady && !workspace.chatLoading
                                    property bool historyReady: !workspace.chatLoading
                                    property bool followPending: false
                                    function scheduleFollow() {
                                        if (workspace.chatLoading || followPending) return
                                        if (!window.followOutput && historyReady) return
                                        followPending = true
                                        Qt.callLater(finishFollow)
                                    }
                                    function finishFollow() {
                                        if (!workspace.chatLoading) {
                                            if (window.followOutput || !historyReady) {
                                                forceLayout()
                                                positionViewAtEnd()
                                                forceLayout()
                                                positionViewAtEnd()
                                            }
                                            historyReady = true
                                        }
                                        followPending = false
                                    }
                                    property int appearanceRevision: appearance.revision + localization.revision
                                    property int readingAnchorIndex: -1
                                    property real readingAnchorOffset: 0
                                    property bool appearanceRelayout: false
                                    onAppearanceRevisionChanged: preserveReadingAnchor()
                                    onWidthChanged: preserveReadingAnchor()
                                    onContentYChanged: rememberReadingAnchor()
                                    function rememberReadingAnchor() {
                                        if (window.followOutput || appearanceRelayout) return
                                        var row = indexAt(width / 2, contentY + 1)
                                        if (row < 0) row = indexAt(width / 2, contentY + spacing + 1)
                                        var visibleItem = itemAtIndex(row)
                                        if (visibleItem) {
                                            readingAnchorIndex = row
                                            readingAnchorOffset = visibleItem.y - contentY
                                        }
                                    }
                                    function preserveReadingAnchor() {
                                        if (window.followOutput || appearanceRelayout || readingAnchorIndex < 0) return
                                        var row = readingAnchorIndex
                                        var offset = readingAnchorOffset
                                        appearanceRelayout = true
                                        cancelFlick()
                                        Qt.callLater(function() {
                                            conversation.forceLayout()
                                            var visibleItem = conversation.itemAtIndex(row)
                                            if (visibleItem) {
                                                var minimum = conversation.originY
                                                var maximum = Math.max(minimum, minimum + conversation.contentHeight - conversation.height)
                                                conversation.contentY = Math.max(minimum, Math.min(maximum, visibleItem.y - offset))
                                            }
                                            conversation.appearanceRelayout = false
                                            conversation.rememberReadingAnchor()
                                        })
                                    }
                                    boundsBehavior: Flickable.StopAtBounds
                                    ScrollBar.vertical: KScrollBar {
                                        id: conversationBar; objectName: "conversationScrollBar"
                                        onPressedChanged: {
                                            if (pressed) { conversation.cancelFlick(); window.followOutput = false }
                                            else window.followOutput = conversation.atYEnd
                                        }
                                    }
                                    onMovementStarted: window.followOutput = false
                                    onMovementEnded: window.followOutput = atYEnd
                                    onCountChanged: scheduleFollow()
                                    onContentHeightChanged: scheduleFollow()
                                    // Keep the inspected card anchored while its height changes.
                                    // A deferred follow-to-end must also observe followOutput.
                                    function toggleOutput(item) {
                                        cancelFlick()
                                        window.followOutput = false
                                        var offset = item.y - contentY
                                        if (item.outputExpanded) offset = Math.max(0, offset)
                                        var row = item.index
                                        transcriptModel.setOutputExpanded(row, !item.outputExpanded)
                                        function restoreAnchor() {
                                            if (item.index !== row) return
                                            conversation.forceLayout()
                                            var minimum = conversation.originY
                                            var maximum = Math.max(minimum, minimum + conversation.contentHeight - conversation.height)
                                            conversation.contentY = Math.max(minimum, Math.min(maximum, item.y - offset))
                                        }
                                        restoreAnchor()
                                        Qt.callLater(restoreAnchor)
                                    }
                                    function toggleReceipt(item) {
                                        cancelFlick()
                                        window.followOutput = false
                                        var row = item.index
                                        var offset = Math.max(0, item.y - contentY)
                                        transcriptModel.setReceiptExpanded(row, !item.receiptExpanded)
                                        function restoreAnchor() {
                                            if (item.index !== row) return
                                            conversation.forceLayout()
                                            var minimum = conversation.originY
                                            var maximum = Math.max(minimum, minimum + conversation.contentHeight - conversation.height)
                                            conversation.contentY = Math.max(minimum, Math.min(maximum, item.y - offset))
                                        }
                                        restoreAnchor()
                                        Qt.callLater(restoreAnchor)
                                    }
                                    function toggleToolGroup(item) {
                                        cancelFlick()
                                        window.followOutput = false
                                        var row = item.index
                                        var offset = item.y - contentY
                                        transcriptModel.setToolGroupExpanded(row, !item.toolGroupExpanded)
                                        function restoreAnchor() {
                                            if (item.index !== row) return
                                            conversation.forceLayout()
                                            var minimum = conversation.originY
                                            var maximum = Math.max(minimum, minimum + conversation.contentHeight - conversation.height)
                                            conversation.contentY = Math.max(minimum, Math.min(maximum, item.y - offset))
                                            conversation.rememberReadingAnchor()
                                        }
                                        restoreAnchor()
                                        Qt.callLater(restoreAnchor)
                                    }
                                    delegate: Item {
                                        id: entry
                                        objectName: "messageEntry_" + index
                                        required property string kind
                                        required property string title
                                        required property string body
                                        required property string previewBody
                                        required property bool previewTruncated
                                        required property bool outputExpanded
                                        required property string status
                                        required property string runId
                                        required property string toolId
                                        required property string command
                                        required property string raw
                                        required property var attachments
                                        required property bool toolGroupStart
                                        required property int toolGroupCount
                                        required property bool toolGroupExpanded
                                        required property bool toolGroupHidden
                                        required property bool toolGroupAttention
                                        required property string toolGroupStatus
                                        required property string receiptLabel
                                        required property string receiptTarget
                                        required property string receiptError
                                        required property string receiptState
                                        required property bool receiptExpanded
                                        required property int index
                                        property bool showSource: false
                                        property bool pooled: false
                                        // Cached and pooled delegates must never animate offscreen.
                                        readonly property bool motionAvailable: kind === "tool" && window.chatMotionEnabled && !pooled
                                            && visible && conversation.opacity > 0 && conversation.height > 0
                                            && workspace.selectedActiveRunId === runId && runId.length > 0
                                        property bool viewportUpdatePending: false
                                        property bool receiptInViewport: false
                                        property bool groupInViewport: false
                                        function intersectsViewport(localY, itemHeight) {
                                            var top = y + localY
                                            return top + itemHeight > conversation.contentY
                                                && top < conversation.contentY + conversation.height
                                        }
                                        function scheduleViewportUpdate() {
                                            if (!motionAvailable) {
                                                receiptInViewport = false; groupInViewport = false
                                                return
                                            }
                                            if (viewportUpdatePending) return
                                            viewportUpdatePending = true
                                            Qt.callLater(updateViewport)
                                        }
                                        function updateViewport() {
                                            viewportUpdatePending = false
                                            if (!motionAvailable) {
                                                receiptInViewport = false; groupInViewport = false
                                                return
                                            }
                                            // Sample visibility and settled bounds outside the Control's
                                            // motion binding while list rows are being rebuilt.
                                            receiptInViewport = toolReceipt.visible && card.visible
                                                && intersectsViewport(card.y + messageColumn.y + toolReceipt.y, toolReceipt.height)
                                            groupInViewport = groupHeader.visible && intersectsViewport(groupHeader.y, groupHeader.height)
                                        }
                                        Component.onCompleted: scheduleViewportUpdate()
                                        onYChanged: scheduleViewportUpdate()
                                        onHeightChanged: scheduleViewportUpdate()
                                        onWidthChanged: scheduleViewportUpdate()
                                        onMotionAvailableChanged: scheduleViewportUpdate()
                                        Connections {
                                            target: conversation
                                            enabled: entry.motionAvailable
                                            function onContentYChanged() { entry.scheduleViewportUpdate() }
                                            function onHeightChanged() { entry.scheduleViewportUpdate() }
                                        }
                                        readonly property bool stateNeedsAttention: ["failed", "interrupted", "cancelled", "errore", "incompleto"].indexOf(status) >= 0
                                        readonly property real previewHeight: Math.max(1, Math.floor(Math.min(144, Math.max(messageFontMetrics.lineSpacing, conversation.height - 100)) / Math.ceil(messageFontMetrics.lineSpacing))) * Math.ceil(messageFontMetrics.lineSpacing)
                                        FontMetrics { id: messageFontMetrics; font: messageText.font }
                                        ListView.onPooled: pooled = true
                                        ListView.onReused: { pooled = false; showSource = false }
                                        width: Math.min(conversation.width - conversationBar.width - 8, appearance.fullWidth ? Infinity : 900)
                                        x: Math.max(0, (conversation.width - conversationBar.width - 8 - width) / 2)
                                        visible: !toolGroupHidden
                                        height: toolGroupHidden ? 0 : (groupHeader.visible ? groupHeader.height : 0)
                                            + (card.visible ? card.implicitHeight + (groupHeader.visible ? 6 : 0) : 0)
                                            + (entry.kind === "tool" ? 3 : window.compact ? 12 : 22)
                                        ToolGroupHeader {
                                            id: groupHeader
                                            objectName: "toolGroupToggle_" + entry.index
                                            width: parent.width
                                            visible: entry.toolGroupStart
                                            operationCount: entry.toolGroupCount
                                            expanded: entry.toolGroupExpanded
                                            needsAttention: entry.toolGroupAttention
                                            outcome: entry.toolGroupStatus
                                            motionEnabled: entry.motionAvailable && entry.groupInViewport
                                            onClicked: conversation.toggleToolGroup(entry)
                                        }
                                        Rectangle {
                                            id: card; width: entry.kind === "user" ? Math.min(parent.width * 0.82, Math.max(parent.width * 0.5, messageText.implicitWidth + 30)) : parent.width
                                            x: entry.kind === "user" ? parent.width - width : 0
                                            visible: entry.toolGroupCount === 0 || entry.toolGroupExpanded
                                            y: groupHeader.visible ? groupHeader.height + 6 : 0
                                            implicitHeight: messageColumn.implicitHeight + (entry.kind === "tool" ? 0 : entry.kind === "state" ? 20 : 26)
                                            radius: entry.kind === "user" ? 18 : 14
                                            color: entry.kind === "user" ? appearance.colors.surfaceAlt : entry.kind === "state" || entry.kind === "team" ? appearance.colors.surface : "transparent"
                                            border.width: 0; border.color: appearance.colors.border
                                            ColumnLayout {
                                                id: messageColumn
                                                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                                anchors.margins: entry.kind === "tool" ? 0 : entry.kind === "state" ? 10 : 13
                                                spacing: entry.kind === "state" ? 6 : 9
                                                ToolReceipt {
                                                    id: toolReceipt
                                                    objectName: "toolReceipt_" + entry.index
                                                    visible: entry.kind === "tool"
                                                    Layout.fillWidth: true
                                                    operationLabel: entry.receiptLabel || entry.title
                                                    target: entry.receiptTarget
                                                    errorPreview: entry.receiptError
                                                    outcome: entry.receiptState
                                                    motionEnabled: entry.motionAvailable && entry.receiptInViewport
                                                    expanded: entry.receiptExpanded
                                                    onClicked: conversation.toggleReceipt(entry)
                                                }
                                                RowLayout {
                                                    visible: entry.kind !== "tool" || entry.receiptExpanded
                                                    Layout.fillWidth: true
                                                    Rectangle {
                                                        visible: entry.kind === "state"
                                                        implicitWidth: stateTitle.implicitWidth + 16
                                                        implicitHeight: 23
                                                        radius: 7
                                                        color: entry.stateNeedsAttention ? appearance.colors.dangerBackground : appearance.colors.successBackground
                                                        Text {
                                                            font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.italic: appearance.uiFont.italic
                                                            id: stateTitle; objectName: "stateTitle_" + entry.index
                                                            anchors.centerIn: parent
                                                            textFormat: Text.PlainText; text: entry.title
                                                            color: entry.stateNeedsAttention ? appearance.colors.danger : appearance.colors.success
                                                            font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2); font.weight: Font.DemiBold
                                                        }
                                                    }
                                                    Image { visible: entry.kind === "assistant"; source: "../assets/kira-mascot.png"; sourceSize.width: 48; sourceSize.height: 48; Layout.preferredWidth: 20; Layout.preferredHeight: 20; fillMode: Image.PreserveAspectFit; smooth: true; mipmap: true; Accessible.ignored: true }
                                                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.italic: appearance.uiFont.italic; visible: entry.kind !== "state" && entry.kind !== "user"; textFormat: Text.PlainText; text: entry.kind === "tool" ? "⌘  " + entry.title : entry.title; color: entry.kind === "assistant" ? appearance.colors.textSecondary : accent; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2); font.weight: Font.DemiBold; font.letterSpacing: entry.kind === "assistant" ? 1.2 : 0; Layout.fillWidth: true; elide: Text.ElideRight }
                                                    Item { visible: entry.kind === "user"; Layout.fillWidth: true }
                                                    Item { visible: entry.kind === "state"; Layout.fillWidth: true }
                                                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; objectName: "messageStatus_" + entry.index; Layout.maximumWidth: entry.kind === "user" ? messageColumn.width * 0.6 : Infinity; Layout.minimumWidth: 0; wrapMode: Text.Wrap; textFormat: Text.PlainText; text: { localization.revision; return qsTr(entry.status) } color: entry.status === "errore" || entry.status === "incompleto" ? appearance.colors.danger : muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); visible: entry.kind !== "state" }
                                                    KButton { objectName: "messageDetails_" + entry.index; text: entry.kind === "state" ? qsTr("Dettagli") : qsTr("Dati"); subtle: true; implicitHeight: 23 + Math.max(0, appearance.uiFont.pixelSize - 13); implicitWidth: entry.kind === "state" ? 65 : 38; visible: entry.raw.length > 0; Accessible.name: entry.kind === "state" ? qsTr("Apri dettagli esecuzione") : entry.kind === "team" ? qsTr("Apri dati del team") : qsTr("Apri dati dello strumento"); onClicked: { window.eventDetails = entry.raw; window.eventDetailsIsRun = entry.kind === "state"; eventDialog.open() } }
                                                    KButton { objectName: "commandCopy_" + entry.index; text: qsTr("Copia comando"); subtle: true; implicitHeight: 23 + Math.max(0, appearance.uiFont.pixelSize - 13); implicitWidth: Math.ceil(105 * appearance.uiFont.pixelSize / 13); visible: entry.kind === "tool" && entry.command.length > 0; onClicked: workspace.copy(entry.command) }
                                                    KButton { objectName: "messageSource_" + entry.index; text: entry.showSource ? qsTr("Formato") : qsTr("Testo"); subtle: true; implicitHeight: 23 + Math.max(0, appearance.uiFont.pixelSize - 13); implicitWidth: Math.ceil(62 * appearance.uiFont.pixelSize / 13); visible: entry.kind === "assistant" && entry.body.length > 0; onClicked: entry.showSource = !entry.showSource; Accessible.name: entry.showSource ? qsTr("Mostra messaggio formattato") : qsTr("Mostra Markdown originale") }
                                                    KButton { objectName: "messageOpenTeam_" + entry.index; text: qsTr("Apri Team"); subtle: true; visible: entry.kind === "team" && entry.command.length > 0; onClicked: assistant.openTeam(entry.command) }
                                                    KButton { objectName: "messageCopy_" + entry.index; text: qsTr("Copia"); subtle: true; implicitHeight: 23 + Math.max(0, appearance.uiFont.pixelSize - 13); implicitWidth: Math.ceil(48 * appearance.uiFont.pixelSize / 13); visible: entry.body.length > 0; onClicked: workspace.copy(entry.body); Accessible.name: qsTr("Copia messaggio o risultato originale") }
                                                }
                                                ImageAttachments { images: entry.attachments; Layout.fillWidth: true; Layout.preferredHeight: implicitHeight }
                                                ReadText {
                                                    id: messageText
                                                    objectName: "messageText_" + entry.index
                                                    Layout.fillWidth: true
                                                    visible: entry.body.length > 0 && (entry.kind !== "tool" || entry.receiptExpanded)
                                                    Layout.maximumHeight: entry.kind === "tool" && !entry.outputExpanded ? entry.previewHeight : Infinity
                                                    clip: entry.kind === "tool" && !entry.outputExpanded
                                                    textFormat: entry.kind === "assistant" && !entry.showSource ? TextEdit.RichText : TextEdit.PlainText
                                                    text: entry.kind === "assistant" && !entry.showSource ? markdownRenderer.renderForAppearance(entry.body, appearance.revision + localization.revision) : entry.kind === "tool" && !entry.outputExpanded ? entry.previewBody : entry.body
                                                    onLinkActivated: function(link) { workspace.openMessageLink(link) }
                                                    font.family: entry.kind === "tool" || entry.showSource ? appearance.codeFont.family : appearance.contentFont.family
                                                    font.styleName: entry.kind === "tool" || entry.showSource ? appearance.codeFont.styleName : appearance.contentFont.styleName
                                                    font.weight: entry.kind === "tool" || entry.showSource ? appearance.codeFont.weight : appearance.contentFont.weight
                                                    font.italic: entry.kind === "tool" || entry.showSource ? appearance.codeFont.italic : appearance.contentFont.italic
                                                    font.pixelSize: entry.kind === "tool" || entry.showSource ? appearance.codeFont.pixelSize : entry.kind === "state" ? Math.max(11, window.chatFontSize - 2) : window.chatFontSize
                                                    selectionColor: appearance.colors.selection
                                                    selectedTextColor: appearance.colors.selectedText
                                                    color: entry.kind === "state" ? (entry.stateNeedsAttention ? appearance.colors.danger : muted) : entry.kind === "tool" || entry.showSource ? appearance.colors.codeText : appearance.colors.text
                                                }
                                                KButton {
                                                    objectName: "outputToggle_" + entry.index
                                                    text: entry.outputExpanded ? qsTr("Riduci") : qsTr("Mostra tutto")
                                                    visible: entry.kind === "tool" && entry.receiptExpanded && (entry.previewTruncated || entry.outputExpanded || messageText.implicitHeight > entry.previewHeight)
                                                    subtle: true
                                                    implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                                                    Layout.alignment: Qt.AlignLeft
                                                    onClicked: conversation.toggleOutput(entry)
                                                    Accessible.name: entry.outputExpanded ? qsTr("Riduci risultato dello strumento") : qsTr("Mostra tutto il risultato dello strumento")
                                                    toolTipText: entry.outputExpanded ? qsTr("Torna all'anteprima") : qsTr("Apri il risultato disponibile nella cronologia")
                                                }
                                            }
                                        }
                                    }
                                }
                                Flickable {
                                    id: emptyConversationScroll
                                    objectName: "emptyConversationScroll"
                                    anchors.fill: parent; anchors.margins: 23; anchors.bottomMargin: 10
                                    visible: conversation.count === 0 && conversation.historyReady && !workspace.chatLoading
                                    clip: true
                                    contentWidth: width
                                    contentHeight: Math.max(height, emptyConversationHint.implicitHeight)
                                    boundsBehavior: Flickable.StopAtBounds
                                    ScrollBar.vertical: KScrollBar {}
                                    ColumnLayout {
                                        id: emptyConversationHint
                                        objectName: "emptyConversationHint"
                                        width: Math.min(emptyConversationScroll.width, 520)
                                        x: Math.max(0, (emptyConversationScroll.width - width) / 2)
                                        y: Math.max(0, (emptyConversationScroll.height - implicitHeight) / 2)
                                        spacing: 12
                                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Ripartiamo da qui."); color: appearance.colors.text; font.pixelSize: appearance.uiFont.pixelSize + 12; font.weight: Font.Medium; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Chiedi di leggere un file, applicare una modifica e controllare il risultato. Vedrai le chiamate agli strumenti e gli esiti confermati dal servizio."); color: muted; font.pixelSize: appearance.uiFont.pixelSize; wrapMode: Text.WordWrap; lineHeight: 1.45; Layout.fillWidth: true }
                                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Seleziona modello e permessi prima di iniziare."); color: accent; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2); Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                    }
                                }
                                Text {
                                    objectName: "chatHistoryLoading"
                                    anchors.centerIn: parent
                                    visible: workspace.chatLoading || !conversation.historyReady
                                    text: qsTr("Caricamento della chat…")
                                    textFormat: Text.PlainText
                                    color: muted
                                    font: appearance.uiFont
                                }
                                KButton { anchors.right: parent.right; anchors.rightMargin: 35; anchors.bottom: parent.bottom; anchors.bottomMargin: 16; text: qsTr("↓  Ultimi aggiornamenti"); visible: !window.followOutput && conversation.historyReady && !workspace.chatLoading; onClicked: { window.followOutput = true; conversation.positionViewAtEnd() } }
                            }
                            RunErrorPanel {
                                failure: workspace.runFailure
                                Layout.fillWidth: true; Layout.leftMargin: 24; Layout.rightMargin: 24; Layout.bottomMargin: 8
                                Layout.maximumWidth: appearance.fullWidth ? Infinity : 900
                                Layout.alignment: Qt.AlignHCenter
                                onDetailsRequested: function(details) {
                                    window.eventDetails = details
                                    window.eventDetailsIsRun = true
                                    eventDialog.open()
                                }
                            }
                            ReasoningSummary {
                                Layout.fillWidth: true; Layout.leftMargin: 24; Layout.rightMargin: 24; Layout.bottomMargin: 4
                                Layout.maximumWidth: appearance.fullWidth ? Infinity : 900
                                Layout.alignment: Qt.AlignHCenter
                                maximumBodyHeight: Math.max(64, Math.min(180, chatColumn.height * 0.22))
                            }
                            GridLayout {
                                id: activityStatus
                                objectName: "activityStatusRow"
                                readonly property bool stacked: width < 420 + Math.max(0, appearance.uiFont.pixelSize - 13) * 28
                                visible: workspace.busy || workspace.activityCanInspect || !workspace.connected || !!workbench.autonomousGoal.id
                                Layout.fillWidth: true; Layout.leftMargin: 24; Layout.rightMargin: 24; Layout.bottomMargin: 6
                                Layout.maximumWidth: appearance.fullWidth ? Infinity : 900
                                Layout.alignment: Qt.AlignHCenter
                                columns: stacked ? 1 : 3; columnSpacing: 8; rowSpacing: 2
                                ActivityIndicator {
                                    objectName: "activityIndicator"
                                    Layout.fillWidth: true; Layout.minimumWidth: 0
                                    Layout.maximumWidth: implicitWidth
                                    label: workbench.activityLabel
                                    outcome: workbench.activityOutcome
                                    motionEnabled: window.chatMotionEnabled
                                    startedAt: workspace.activityStartedAt
                                    animate: workspace.activityAnimating && window.chatMotionEnabled
                                    clockEnabled: workspace.connected && window.visible && window.visibility !== Window.Minimized && window.page === 0
                                }
                                Text {
                                    objectName: "activityToolAttention"
                                    text: workspace.activityAttentionSummary
                                    visible: text.length > 0
                                    Layout.fillWidth: true; Layout.minimumWidth: 0
                                    font: appearance.uiFont; color: appearance.colors.warning
                                    wrapMode: Text.Wrap; textFormat: Text.PlainText
                                }
                                Item { Layout.fillWidth: true; Layout.minimumWidth: 0; visible: !activityStatus.stacked && !workspace.activityHasToolIssues }
                                RowLayout {
                                    Layout.alignment: Qt.AlignRight; spacing: 8
                                KButton {
                                    objectName: "activityDetailsButton"; text: qsTr("Dettagli")
                                    implicitHeight: 32 + Math.max(0, appearance.uiFont.pixelSize - 13)
                                    subtle: true; enabled: workspace.activityCanInspect
                                    onClicked: { window.eventDetails = workspace.activityDetails; window.eventDetailsIsRun = true; eventDialog.open() }
                                    toolTipText: qsTr("Stato e dati registrati dell’esecuzione")
                                }
                                KButton {
                                    objectName: "stopRunButton"; text: qsTr("Ferma"); iconName: "stop"
                                    implicitHeight: 32 + Math.max(0, appearance.uiFont.pixelSize - 13)
                                    destructive: true; visible: workspace.busy
                                    toolTipText: qsTr("Ferma l’esecuzione in corso")
                                    enabled: workspace.connected && workspace.selectedActiveRunId.length > 0
                                    onClicked: workspace.stop()
                                }
                                }
                            }
                            Rectangle {
                                objectName: "composerCard"
                                Layout.fillWidth: true; Layout.leftMargin: 24; Layout.rightMargin: 24; Layout.bottomMargin: 5
                                Layout.maximumWidth: appearance.fullWidth ? Infinity : 900
                                Layout.alignment: Qt.AlignHCenter
                                implicitHeight: composerColumn.implicitHeight + (window.compact ? 16 : 20)
                                color: appearance.colors.surfaceAlt; radius: 14
                                border.color: prompt.activeFocus ? appearance.colors.accent : appearance.colors.border; border.width: 1
                                ColumnLayout {
                                    id: composerColumn; anchors.fill: parent; anchors.margins: window.compact ? 8 : 10; spacing: window.compact ? 4 : 6
                                    Text { visible: workbench.mode !== "execute"; Layout.fillWidth: true; text: workbench.mode === "plan" ? qsTr("Pianifica in sola lettura. Per applicare il piano scegli Esegui e invia una nuova richiesta.") : workbench.mode === "review" ? qsTr("Revisione in sola lettura: controlla i problemi senza modificare file.") : qsTr("Riepiloga produce una risposta; non riduce il contesto del provider."); color: appearance.colors.muted; wrapMode: Text.Wrap; font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 2); font.family: appearance.uiFont.family; textFormat: Text.PlainText }
                                    Text { visible: !workbench.modeAvailable; text: qsTr("Questa modalità non è dichiarata disponibile dal provider selezionato. Aggiorna il catalogo o scegli un’altra modalità."); Layout.fillWidth: true; wrapMode: Text.Wrap; color: appearance.colors.warning; font: appearance.uiFont; textFormat: Text.PlainText }
                                    Text { visible: workbench.sessionArchived; text: qsTr("Chat archiviata: ripristinala da Strumenti della chat per continuare."); Layout.fillWidth: true; wrapMode: Text.Wrap; color: appearance.colors.warning; font: appearance.uiFont; textFormat: Text.PlainText }
                                    Text {
                                        objectName: "conversationAvailabilityNotice"
                                        visible: workbench.conversationNotice.length > 0
                                        text: workbench.conversationNotice
                                        Layout.fillWidth: true; wrapMode: Text.Wrap; maximumLineCount: 3
                                        color: appearance.colors.muted; font: appearance.uiFont; textFormat: Text.PlainText
                                    }
                                    ImageAttachments { objectName: "draftImageAttachments"; images: workspace.draftImages; editable: true; Layout.fillWidth: true; Layout.preferredHeight: implicitHeight }
                                    Text {
                                        visible: workspace.draftImages.length > 0 && !workspace.imageInputsEnabled; Layout.fillWidth: true
                                        text: qsTr("Il modello selezionato non supporta immagini. Cambia modello o rimuovi le immagini prima di inviare.")
                                        color: appearance.colors.muted; font: appearance.uiFont; textFormat: Text.PlainText; wrapMode: Text.Wrap
                                    }
                                    ScrollView {
                                        id: composerScroll; objectName: "composerScroll"
                                        readonly property real minimumInputHeight: Math.ceil(promptMetrics.lineSpacing * (window.compact ? 1 : 2) + prompt.topPadding + prompt.bottomPadding)
                                        readonly property real maximumInputHeight: Math.max(minimumInputHeight,
                                            Math.min(workspace.draftImages.length > 0 && window.height < 800 ? 65 : window.compact ? 95 : 130, window.height * 0.18))
                                        readonly property real inputHeight: Math.max(minimumInputHeight, prompt.contentHeight + prompt.topPadding + prompt.bottomPadding)
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: Math.min(maximumInputHeight, inputHeight)
                                        Layout.minimumHeight: minimumInputHeight
                                        clip: true
                                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                                        FontMetrics { id: promptMetrics; font: prompt.font }
                                        AttachmentTextArea {
                                            attachmentContext: JSON.stringify([workspace.project.id || "", workspace.sessionId])
                                            id: prompt; objectName: "prompt"
                                            imageAttachments: workspace.imageInputsEnabled
                                            width: composerScroll.availableWidth
                                            height: composerScroll.inputHeight
                                            placeholderText: workbench.guidanceMode ? qsTr("Aggiungi indicazioni al turno in corso…") : qsTr("Descrivi il prossimo passo…")
                                            onTextChanged: {
                                                workspace.setDraftText(text)
                                                if (!activeFocus || window.restoringDraft) return
                                                var before = text.slice(0, cursorPosition)
                                                if (/(^|\s)\/$/.test(before) && !actionPalette.visible) {
                                                    window.commandStart = before.length - 1
                                                    window.openActions("")
                                                } else if (/(^|\s)@$/.test(before) && !projectFileDialog.visible) {
                                                    window.mentionStart = before.length - 1
                                                    window.openProjectFiles()
                                                }
                                            }
                                            wrapMode: TextEdit.Wrap; selectByMouse: true; color: appearance.colors.text
                                            textFormat: TextEdit.PlainText; background: null
                                            font.pixelSize: window.chatFontSize; leftPadding: 2; rightPadding: 2; topPadding: 4; bottomPadding: 4
                                            Keys.onPressed: function(event) { if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && (event.modifiers & Qt.ControlModifier)) { window.submitComposer(); event.accepted = true } }
                                        }
                                    }
                                    Text {
                                        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic
                                        objectName: "promptAttachmentStatus"
                                        Layout.fillWidth: true
                                        visible: prompt.attachmentBusy || prompt.attachmentError.length > 0
                                        text: prompt.attachmentBusy ? qsTr("Preparazione allegato…") : prompt.attachmentError
                                        color: prompt.attachmentBusy ? muted : appearance.colors.danger
                                        font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2); textFormat: Text.PlainText; wrapMode: Text.Wrap
                                    }
                                    // Two groups: what the message carries, then how it goes out.
                                    // Narrow columns stack them instead of overflowing the card.
                                    GridLayout {
                                        id: composerChips; objectName: "composerChips"
                                        Layout.fillWidth: true; columnSpacing: 4; rowSpacing: 4
                                        readonly property bool narrow: chatColumn.width < 700
                                        // Labels give way before the rows stack, so the model chip keeps its text.
                                        readonly property bool tight: chatColumn.width < 820
                                        columns: narrow ? 1 : 2
                                        RowLayout {
                                        Layout.fillWidth: true; spacing: 4
                                        KButton {
                                            objectName: "promptAttach"
                                            iconName: "attach"; subtle: true
                                            Accessible.name: qsTr("Allega")
                                            implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13); implicitWidth: 34
                                            enabled: !prompt.attachmentBusy
                                            onClicked: prompt.chooseAttachments()
                                            toolTipText: qsTr("Allega") + " · " + (workspace.imageInputsEnabled ? qsTr("Le immagini saranno inviate solo con il messaggio. La disponibilità della visione dipende dal modello.") : qsTr("Scegli file, oppure incolla o trascina file e immagini nel messaggio"))
                                        }
                                        KButton { objectName: "projectFilesButton"; implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13); cornerRadius: 15; text: composerChips.tight ? "" : qsTr("File"); iconName: "file"; subtle: true; Accessible.name: qsTr("File del progetto"); onClicked: { window.mentionStart = -1; window.openProjectFiles() } toolTipText: qsTr("Cerca file del progetto · @ nel messaggio") }
                                        KComboBox {
                                            objectName: "composerModeChoice"; chip: true; implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                                            // Shares the row's slack with the model chip and elides before overflowing.
                                            Layout.fillWidth: true; Layout.minimumWidth: 64
                                            Layout.preferredWidth: (composerChips.tight ? 118 : 132) + Math.max(0, appearance.uiFont.pixelSize - 13) * 4
                                            Layout.maximumWidth: Layout.preferredWidth
                                            model: [qsTr("Esegui"), qsTr("Pianifica"), qsTr("Revisione"), qsTr("Riepiloga")]
                                            currentIndex: ["execute", "plan", "review", "recap"].indexOf(workbench.mode)
                                            Accessible.name: qsTr("Modalità della prossima richiesta")
                                            onActivated: workbench.setMode(["execute", "plan", "review", "recap"][index])
                                        }
                                        KButton {
                                            id: modelChip; objectName: "modelControlsToggle"
                                            subtle: true; quietSelection: true; checked: modelPopover.visible; cornerRadius: 15
                                            implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                                            // The chip takes the slack of the row and shrinks to its icon first.
                                            Layout.fillWidth: true; Layout.minimumWidth: 46
                                            Layout.preferredWidth: Math.min(composerChips.tight ? 260 : 340, modelChipMetrics.advanceWidth + 72)
                                            Layout.maximumWidth: Layout.preferredWidth
                                            TextMetrics { id: modelChipMetrics; text: modelChipLabel.selectionSummary; font: modelChip.font }
                                            onClicked: modelPopover.visible ? modelPopover.close() : modelPopover.open()
                                            toolTipText: modelChipLabel.fullSummary + (workspace.accountLabel ? " · " + workspace.accountLabel : "")
                                            Accessible.name: modelPopover.visible ? qsTr("Nascondi opzioni del modello") : qsTr("Mostra opzioni del modello")
                                            contentItem: Item {
                                                KIcon { name: "models"; ink: modelChip.foreground; width: 16; height: 16; anchors.left: parent.left; anchors.leftMargin: 10; anchors.verticalCenter: parent.verticalCenter }
                                                Text {
                                                    id: modelChipLabel; objectName: "modelControlsSummary"
                                                    readonly property string details: (workspace.selectedModel || qsTr("Scegli un modello")) + (kformat.offersEffort(workspace.backend) ? " · " + (workspace.selectedEffort || qsTr("Predefinito del provider")) : "")
                                                    // The chip shows the short provider name; the tooltip keeps the full label.
                                                    readonly property string selectionSummary: workspace.backendShortLabel + " · " + details
                                                    readonly property string fullSummary: workspace.backendLabel + " · " + details
                                                    anchors.fill: parent; leftPadding: 32; rightPadding: 26
                                                    text: selectionSummary; textFormat: Text.PlainText; elide: Text.ElideRight
                                                    font: modelChip.font; color: modelChip.foreground; verticalAlignment: Text.AlignVCenter
                                                }
                                                KIcon { name: "chevron-down"; ink: modelChip.foreground; width: 12; height: 12; anchors.right: parent.right; anchors.rightMargin: 9; anchors.verticalCenter: parent.verticalCenter }
                                            }
                                        }
                                        }
                                        RowLayout {
                                        Layout.alignment: Qt.AlignRight | Qt.AlignVCenter; spacing: 4
                                        KButton { objectName: "projectQueueButton"; implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13); cornerRadius: 15; iconName: "queue"; text: composerChips.tight ? (workbench.pendingCount > 0 ? String(workbench.pendingCount) : "") : qsTr("Coda %1").arg(workbench.pendingCount); subtle: true; Accessible.name: (workbench.pendingCount === 1 ? qsTr("Coda del progetto: %1 richiesta") : qsTr("Coda del progetto: %1 richieste")).arg(workbench.pendingCount); toolTipText: qsTr("Coda del progetto · controlla, modifica e riordina le richieste"); onClicked: workbenchDialog.openSection("queue") }
                                        KButton { objectName: "chatToolsButton"; implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13); iconName: "more"; subtle: true; implicitWidth: 34; Accessible.name: qsTr("Strumenti della chat"); toolTipText: qsTr("Cronologia, obiettivo, modifiche e gestione chat"); onClicked: { window.commandStart = -1; window.openActions("") } }
                                        KButton {
                                            objectName: "queueComposerButton"; text: qsTr("Accoda")
                                            subtle: true; implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                                            visible: workspace.busy || teams.busy || assistant.active || workbench.guidanceRetryAvailable
                                            toolTipText: qsTr("Accoda una richiesta separata; non cambia il turno attivo")
                                            enabled: workbench.composerReady && workbench.modeAvailable && !workbench.sessionArchived && !prompt.attachmentBusy && (prompt.text.trim().length > 0 || workspace.draftImages.length > 0) && (workspace.draftImages.length === 0 || workspace.imageInputsEnabled) && workspace.selectedModel.length > 0
                                            onClicked: workbench.enqueue(prompt.text)
                                        }
                                        KButton {
                                            id: sendButton; objectName: "sendButton"
                                            // Guidance and retry keep their words: they change what the message does.
                                            iconName: workbench.guidanceRetryAvailable || workbench.guidanceMode ? "" : "send"
                                            text: workbench.guidanceRetryAvailable ? qsTr("Verifica invio") : workbench.guidanceMode ? qsTr("Invia indicazioni") : ""
                                            primary: true; cornerRadius: 15
                                            implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                                            toolTipText: workbench.guidanceRetryAvailable ? qsTr("Verifica la stessa richiesta con la sua identità originale") : workbench.guidanceMode ? qsTr("Invia indicazioni senza interrompere lo strumento in corso · Ctrl+Invio") : qsTr("Invia · Ctrl+Invio")
                                            Accessible.name: workbench.guidanceRetryAvailable ? qsTr("Verifica invio") : workbench.guidanceMode ? qsTr("Invia indicazioni") : qsTr("Invia")
                                            enabled: (workbench.canSubmit || workbench.goalCommandReady(prompt.text)) && !prompt.attachmentBusy && (prompt.text.trim().length > 0 || workspace.draftImages.length > 0) && (workspace.draftImages.length === 0 || workspace.imageInputsEnabled) && workspace.selectedModel.length > 0
                                            onClicked: window.submitComposer()
                                        }
                                        }
                                    }
                                    Text {
                                        objectName: "guidanceDeliveryNotice"
                                        visible: workbench.guidanceNotice.length > 0
                                        text: workbench.guidanceNotice
                                        Layout.fillWidth: true; wrapMode: Text.Wrap
                                        color: workbench.guidanceWarning ? appearance.colors.warning : appearance.colors.accent
                                        font: appearance.uiFont; textFormat: Text.PlainText
                                    }
                                    Text { visible: workbench.draftStatus.length > 0; Layout.fillWidth: true; text: workbench.draftStatus; color: appearance.colors.warning; wrapMode: Text.Wrap; font: appearance.uiFont; textFormat: Text.PlainText }
                                    Text { visible: workbench.error.length > 0 && !workbenchDialog.visible; Layout.fillWidth: true; text: workbench.error; color: appearance.colors.warning; wrapMode: Text.Wrap; font: appearance.uiFont; textFormat: Text.PlainText
                                    }
                                }
                                // Provider, model, effort and session live behind the composer's model chip.
                                Popup {
                                    id: modelPopover; objectName: "modelPopover"
                                    x: 0; y: -height - 8; width: parent.width
                                    padding: 14; modal: false; dim: false; focus: true
                                    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
                                    Component.onCompleted: if (workspace.modelControlsVisible && window.page === 0 && hasProject) Qt.callLater(function() { modelPopover.open() })
                                    background: Rectangle { color: appearance.colors.surfaceRaised; radius: 14; border.color: appearance.colors.border }
                                    contentItem: ColumnLayout {
                                        id: controlsColumn; objectName: "modelControlsExpanded"
                                        spacing: 8
                                        RowLayout {
                                            Layout.fillWidth: true; spacing: 8
                                            Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Provider, modello e ragionamento"); color: appearance.colors.text; font.weight: Font.DemiBold; Layout.fillWidth: true }
                                            KButton { iconName: "refresh"; subtle: true; implicitWidth: 32; implicitHeight: 30; onClicked: workspace.discover(workspace.backend); Accessible.name: qsTr("Aggiorna modelli"); toolTipText: qsTr("Aggiorna modelli") }
                                            KButton { objectName: "modelPopoverClose"; iconName: "close"; subtle: true; implicitWidth: 32; implicitHeight: 30; onClicked: modelPopover.close(); Accessible.name: qsTr("Chiudi le opzioni del modello"); toolTipText: Accessible.name }
                                        }
                                        RowLayout {
                                            Layout.fillWidth: true; spacing: 8
                                            KComboBox {
                                                palette.dark: window.muted
                                                id: backendBox; objectName: "backendBox"
                                                Layout.preferredWidth: ["openai_chatgpt", "deepseek_account", "deepseek_web", "claude_code"].indexOf(workspace.backend) >= 0 ? (chatColumn.width < 650 ? 160 : 255) : 110; implicitHeight: 35
                                                model: ["Ollama", "Codex", qsTr("OpenAI diretto · ChatGPT"), qsTr("DeepSeek Account"), qsTr("DeepSeek Web · sperimentale"), qsTr("Claude Code · uso personale")]
                                                currentIndex: ["ollama", "codex", "openai_chatgpt", "deepseek_account", "deepseek_web", "claude_code"].indexOf(workspace.backend)
                                                onActivated: { workspace.setBackend(["ollama", "codex", "openai_chatgpt", "deepseek_account", "deepseek_web", "claude_code"][currentIndex]); currentIndex = Qt.binding(function() { return ["ollama", "codex", "openai_chatgpt", "deepseek_account", "deepseek_web", "claude_code"].indexOf(workspace.backend) }) }
                                                enabled: !workspace.busy && !assistant.active
                                                Accessible.name: qsTr("Provider")
                                                KToolTip { visible: backendBox.hovered; text: workspace.backendLabel }
                                            }
                                            KComboBox {
                                                palette.dark: window.muted
                                                id: modelBox; objectName: "modelBox"; model: workspace.models; textRole: "name"; valueRole: "id"
                                                Layout.fillWidth: true; implicitHeight: 35
                                                currentIndex: indexOfValue(workspace.selectedModel)
                                                displayText: currentIndex < 0 ? (workspace.selectedModel || qsTr("Scegli un modello…")) : currentText
                                                onActivated: workspace.setModel(currentValue)
                                                enabled: !workspace.busy && !assistant.active
                                                Accessible.name: qsTr("Modello")
                                            }
                                            KComboBox {
                                                palette.dark: window.muted
                                                id: effortBox; objectName: "effortBox"; model: [qsTr("Predefinito del provider")].concat(workspace.efforts)
                                                Layout.preferredWidth: chatColumn.width < 650 ? 122 : 150; implicitHeight: 35
                                                visible: kformat.offersEffort(workspace.backend)
                                                currentIndex: Math.max(0, workspace.efforts.indexOf(workspace.selectedEffort) + 1)
                                                displayText: currentIndex === 0 && workspace.selectedEffort.length > 0 ? qsTr("%1 · salvato").arg(workspace.selectedEffort) : currentText
                                                onActivated: workspace.setEffort(currentIndex === 0 ? "" : currentText)
                                                enabled: !workspace.busy && !assistant.active
                                                Accessible.name: qsTr("Livello di ragionamento")
                                            }
                                        }
                                        RowLayout {
                                            Layout.fillWidth: true; spacing: 8
                                            Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("SESSIONE"); color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 4); font.letterSpacing: 1 }
                                            KComboBox {
                                                palette.dark: window.muted
                                                id: sessionBox; objectName: "sessionBox"; model: workspace.sessions; textRole: "label"; valueRole: "id"
                                                Layout.fillWidth: true; implicitHeight: 28
                                                currentIndex: indexOfValue(workspace.sessionId)
                                                displayText: currentIndex < 0 ? qsTr("Nuova · contesto della memoria condiviso") : currentText
                                                onActivated: workspace.selectSession(currentValue)
                                                enabled: !workspace.busy && !assistant.active
                                                Accessible.name: qsTr("Sessione")
                                            }
                                            CheckBox {
                                                id: ollamaTools; objectName: "ollamaToolsChoice"
                                                HoverHandler { id: ollamaToolsHover; cursorShape: appearance.pointerCursors && parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
                                                font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
                                                text: qsTr("Strumenti"); checked: workspace.toolsEnabled || workspace.backend !== "ollama"
                                                // Changing tools starts another chat, so a saved chat keeps its choice.
                                                visible: workspace.backend === "ollama"; enabled: workspace.canUseTools && !workspace.busy && !workspace.sessionId
                                                onClicked: workspace.setTools(checked)
                                                readonly property string hint: workspace.sessionId ? qsTr("Gli strumenti si scelgono per le nuove chat: apri una nuova chat per cambiarli.") : workspace.canUseTools ? qsTr("La prima esecuzione verifica il supporto del modello; se fallisce resta disponibile la chat.") : qsTr("Strumenti non dichiarati dal modello. La chat resta disponibile.")
                                                Accessible.description: hint
                                                // A disabled control still reports hover to its handler.
                                                KToolTip { visible: ollamaToolsHover.hovered; text: ollamaTools.hint }
                                            }
                                        }
                                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; id: providerLabel; text: workspace.providerText; Layout.fillWidth: true; elide: Text.ElideRight; color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); KToolTip { visible: providerMouse.containsMouse; text: providerLabel.text } MouseArea { id: providerMouse; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton } }
                                    }
                                }
                            }
                            Flow {
                                objectName: "chatToolBar"
                                Layout.fillWidth: true; Layout.leftMargin: 24; Layout.rightMargin: 24
                                Layout.maximumWidth: appearance.fullWidth ? Infinity : 900
                                Layout.alignment: Qt.AlignHCenter
                                spacing: 2
                                ChatToolButton { objectName: "chatFilesButton"; label: qsTr("File"); iconName: "file"; checked: panelHost.activePanel === artifactsDialog; quietSelection: true; onClicked: artifactsDialog.open() }
                                ChatToolButton { objectName: "chatUsageButton"; label: qsTr("Uso"); iconName: "usage"; checked: panelHost.activePanel === metricsDialog; quietSelection: true; onClicked: metricsDialog.open() }
                                ChatToolButton { objectName: "chatContextButton"; label: chatContext.compacting ? qsTr("Compattazione…") : qsTr("Contesto e memoria"); iconName: "context"; enabled: workspace.sessionId.length > 0; checked: panelHost.activePanel === chatContextDialog; quietSelection: true; onClicked: chatContextDialog.open() }
                                ChatToolButton { objectName: "chatKnowledgeButton"; label: qsTr("Conoscenza"); iconName: "knowledge"; enabled: workspace.sessionId.length > 0; onClicked: assistantTeamPanel.showEnvironment(); toolTipText: qsTr("Conoscenza e strumenti") }
                                ChatToolButton { objectName: "chatWebButton"; label: qsTr("Browser"); iconName: "browser"; enabled: workspace.web.available; checked: workspace.web.paneVisible; quietSelection: true; onClicked: { workspace.studio.hideStudio(); workspace.web.showBrowser() } toolTipText: qsTr("Apri il browser integrato") }
                                ChatToolButton { objectName: "chatStudioButton"; label: qsTr("Studio"); iconName: "studio"; visible: workspace.studio.available; enabled: workspace.connected; checked: workspace.studio.paneVisible; quietSelection: true; onClicked: workspace.studio.showStudio(); toolTipText: qsTr("Apri il mondo creativo locale di questo progetto") }
                                ChatToolButton { objectName: "sharedMemoryButton"; label: qsTr("Memoria del progetto"); iconName: "memory"; checked: panelHost.activePanel === sharedMemoryDialog; quietSelection: true; onClicked: sharedMemoryDialog.open() }
                                ChatToolButton { objectName: "chatSoulButton"; label: souls.activeName || qsTr("Anima"); iconName: "anima"; enabled: souls.available; checked: panelHost.activePanel === soulDialog; quietSelection: true; onClicked: soulDialog.open(); toolTipText: qsTr("Anima di Kira · identità e ricordi") + (souls.activeName ? " · " + souls.activeName : "") }
                                KButton {
                                    id: teamStatusButton
                                    objectName: "assistantConfigureTeam"
                                    readonly property bool paused: assistantTeamPanel.current.state === "paused"
                                        || (!!teams.team.id && teams.team.id === assistant.currentTeamId && teams.team.state === "paused")
                                    readonly property string stateText: paused ? qsTr("Team · %1").arg(qsTr("In pausa")) : assistantTeamPanel.hasDelegation ? qsTr("Team · %1").arg(assistant.current.label) : assistant.teamActive ? qsTr("Team al lavoro") : qsTr("Team")
                                    readonly property bool showState: paused || assistantTeamPanel.hasDelegation || assistant.teamActive || workspace.toolLabels
                                    text: showState ? stateText : ""
                                    implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                                    // Measured text keeps the width independent of the live label's layout.
                                    implicitWidth: showState ? Math.min(chatColumn.width < 650 ? 120 : 200, teamMetrics.advanceWidth + 44) : 34
                                    subtle: true; quietSelection: true; checked: assistantTeamPanel.expanded
                                    font.pixelSize: Math.max(11, appearance.uiFont.pixelSize - 1)
                                    TextMetrics { id: teamMetrics; text: teamStatusButton.stateText; font: teamStatusButton.font }
                                    contentItem: Item {
                                        KIcon { name: "team"; ink: teamStatusButton.paused ? appearance.colors.warning : teamStatusButton.foreground; width: 18; height: 18; anchors.left: parent.left; anchors.leftMargin: teamStatusButton.showState ? 10 : (parent.width - width) / 2; anchors.verticalCenter: parent.verticalCenter }
                                        StatusIndicator {
                                            objectName: "teamStatusIndicator"
                                            visible: teamStatusButton.paused
                                            anchors.left: parent.left; anchors.leftMargin: teamStatusButton.showState ? 20 : (parent.width - 18) / 2 + 10
                                            anchors.top: parent.top; anchors.topMargin: 3
                                            state: "paused"; diameter: 10
                                        }
                                        Text {
                                            id: teamStatusLabel
                                            visible: teamStatusButton.showState
                                            anchors.fill: parent
                                            leftPadding: 34; rightPadding: 10
                                            text: teamStatusButton.stateText; font: teamStatusButton.font
                                            color: teamStatusButton.paused ? appearance.colors.warning : teamStatusButton.foreground
                                            textFormat: Text.PlainText; elide: Text.ElideRight
                                            verticalAlignment: Text.AlignVCenter
                                        }
                                    }
                                    background: Rectangle {
                                        radius: 10
                                        color: teamStatusButton.paused ? Qt.rgba(appearance.colors.warning.r, appearance.colors.warning.g, appearance.colors.warning.b, 0.10)
                                            : teamStatusButton.down ? appearance.colors.pressed
                                            : teamStatusButton.hovered || teamStatusButton.checked ? appearance.colors.hover : "transparent"
                                        border.width: teamStatusButton.activeFocus ? 2 : teamStatusButton.paused ? 1 : 0
                                        border.color: teamStatusButton.activeFocus ? appearance.colors.accent
                                            : Qt.rgba(appearance.colors.warning.r, appearance.colors.warning.g, appearance.colors.warning.b, 0.25)
                                    }
                                    onClicked: { if (assistantTeamPanel.hasTeamContext) assistantTeamPanel.toggleDetails(); else assistant.configureTeam() }
                                    toolTipText: paused ? stateText : assistantTeamPanel.hasDelegation ? qsTr("Team · %1").arg(assistant.current.label) : qsTr("Configura un team per questo progetto")
                                    Accessible.name: stateText
                                }
                                ChatToolButton { objectName: "inputOpen"; label: qsTr("Domande"); iconName: "questions"; badge: inputs.pendingCount; primary: inputs.pendingCount > 0; destructive: inputs.error.length > 0; onClicked: inputDialog.open(); toolTipText: inputs.error || qsTr("Domande · Ctrl+Shift+Q") }
                                ChatToolButton { objectName: "chatWebSettingsButton"; label: qsTr("Impostazioni web"); iconName: "settings"; enabled: workspace.web.available; onClicked: webDialog.open() }
                                Item { width: 9; height: 30; Rectangle { anchors.centerIn: parent; width: 1; height: 18; color: appearance.colors.border } }
                                ChatToolButton { objectName: "toolLabelsToggle"; label: qsTr("Etichette"); iconName: "labels"; text: label; checked: workspace.toolLabels; quietSelection: true; onClicked: workspace.setToolLabels(!workspace.toolLabels); toolTipText: qsTr("Mostra o nascondi le etichette accanto alle icone degli strumenti") }
                            }
                            ColumnLayout {
                                objectName: "chatFootnote"
                                Layout.fillWidth: true; Layout.leftMargin: 28; Layout.rightMargin: 28; Layout.bottomMargin: 6
                                Layout.maximumWidth: appearance.fullWidth ? Infinity : 892
                                Layout.alignment: Qt.AlignHCenter
                                spacing: 1
                                GridLayout {
                                    Layout.fillWidth: true; columnSpacing: 12; rowSpacing: 0
                                    columns: chatColumn.width < 850 ? 1 : 2
                                    RowLayout {
                                        id: chatPermissionRow
                                        readonly property bool modeReadOnly: !workspace.selectedActiveRunId && workbench.mode !== "execute"
                                        readonly property bool readOnly: workbench.conversationReadOnly || modeReadOnly
                                        readonly property bool hostAccess: !readOnly && ["trusted_host", "full_access"].indexOf(workspace.project.permission) >= 0
                                        Layout.fillWidth: true; spacing: 5
                                        KIcon { name: "lock"; ink: chatPermissionRow.hostAccess ? appearance.colors.warning : muted; Layout.preferredWidth: 12; Layout.preferredHeight: 12 }
                                        Text {
                                            objectName: "chatPermissionSummary"
                                            font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText
                                            text: workbench.conversationReadOnly ? qsTr("Conversazione in sola lettura") : chatPermissionRow.modeReadOnly ? qsTr("Modalità in sola lettura") : workspace.project.permission === "full_access" ? qsTr("Accesso completo · file oltre il progetto e comandi come il tuo utente") : workspace.project.permission === "trusted_host" ? qsTr("Host fidato · file nel progetto, comandi come il tuo utente") : workspace.project.permission === "project_write" ? qsTr("Scrittura nel progetto · comandi Kira non autorizzati") : qsTr("Sola lettura")
                                            font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 3)
                                            color: chatPermissionRow.hostAccess ? appearance.colors.warning : muted
                                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                                        }
                                    }
                                    RowLayout {
                                        visible: workspace.backend === "openai_chatgpt"
                                        Layout.fillWidth: true; spacing: 6
                                        Text {
                                            objectName: "chatGPTPlanIndicator"
                                            Layout.fillWidth: true
                                            text: workspace.planQuotaLimited ? qsTr("Limite di utilizzo raggiunto") : directAccount.planConnected ? qsTr("Stai usando il piano ChatGPT") : qsTr("Piano ChatGPT · accesso richiesto")
                                            font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 2)
                                            color: workspace.planQuotaLimited ? appearance.colors.warning : appearance.colors.muted
                                            textFormat: Text.PlainText; wrapMode: Text.Wrap
                                        }
                                        KButton {
                                            objectName: "chatGPTPlanUsage"; text: qsTr("Gestisci utilizzo")
                                            implicitHeight: 25 + Math.max(0, appearance.uiFont.pixelSize - 13)
                                            font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 2)
                                            primary: workspace.planQuotaLimited; subtle: !workspace.planQuotaLimited
                                            onClicked: metricsDialog.open()
                                        }
                                    }
                                    RowLayout {
                                        // Claude Code reports its plan windows during each run; Kira shows the last notice.
                                        visible: workspace.backend === "claude_code" && accountUsage.planSummary.length > 0
                                        Layout.fillWidth: true; spacing: 6
                                        Text {
                                            objectName: "claudePlanIndicator"
                                            Layout.fillWidth: true
                                            text: accountUsage.planSummary
                                            font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 2)
                                            color: accountUsage.planWarning ? appearance.colors.warning : appearance.colors.muted
                                            textFormat: Text.PlainText; wrapMode: Text.Wrap
                                        }
                                        KButton {
                                            objectName: "claudePlanUsage"; text: qsTr("Dettagli")
                                            implicitHeight: 25 + Math.max(0, appearance.uiFont.pixelSize - 13)
                                            font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 2)
                                            subtle: true
                                            Accessible.name: qsTr("Dettagli dell’utilizzo del piano Claude")
                                            onClicked: metricsDialog.open()
                                        }
                                    }
                                }
                                Text {
                                    visible: workspace.backend === "openai_chatgpt" && workspace.planQuotaLimited
                                    Layout.fillWidth: true
                                    text: qsTr("Controlla il limite assegnato a Kira o attendi il ripristino del limite prima di riprovare.")
                                    textFormat: Text.PlainText; wrapMode: Text.Wrap
                                    font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; color: appearance.colors.muted
                                }
                            }
                        }
                        BrowserPane {
                            visible: workspace.web.paneVisible
                            SplitView.preferredWidth: Math.max(400, browserSplit.width * 0.55)
                            SplitView.minimumWidth: 340; SplitView.fillWidth: window.browserFocused
                            onSettingsRequested: webDialog.open()
                        }
                        StudioPane {
                            visible: workspace.studio.paneVisible
                            SplitView.preferredWidth: Math.max(400, browserSplit.width * 0.55)
                            SplitView.minimumWidth: 340; SplitView.fillWidth: window.studioFocused
                        }
                        Item {
                            objectName: "workspacePanelSpace"
                            visible: panelHost.reservedWidth > 0
                            SplitView.preferredWidth: panelHost.reservedWidth
                            SplitView.minimumWidth: panelHost.reservedWidth
                            SplitView.maximumWidth: panelHost.reservedWidth
                        }
                        Rectangle {
                            id: detailsPanel; objectName: "detailsPanel"
                            SplitView.preferredWidth: 282; SplitView.minimumWidth: 282
                            color: appearance.colors.surface; radius: 16; border.color: appearance.colors.border
                            visible: window.detailsVisible && window.width >= 1120 && !workspace.web.paneVisible && !workspace.studio.paneVisible && !panelHost.activePanel
                            Loader { anchors.fill: parent; anchors.margins: 0; sourceComponent: projectDetails }
                        }
                    }
                }
                ScrollView {
                    id: modelsScroll; clip: true; contentWidth: availableWidth
                    ColumnLayout {
                        id: modelsPage
                        readonly property real sideMargin: modelsScroll.availableWidth < 900 ? 24 : Math.max(28, (modelsScroll.availableWidth - 960) / 2)
                        // With a chat open, the provider buttons only show and configure a
                        // provider; "Usa modello" is what moves the chat to it.
                        property string viewedBackend: ""
                        readonly property bool foreign: viewedBackend.length > 0 && viewedBackend !== workspace.backend && workspace.sessionId.length > 0
                        readonly property string shownBackend: foreign ? viewedBackend : workspace.backend
                        readonly property var viewedCatalog: { workspace.models; return foreign ? workspace.catalogFor(viewedBackend) : ({}) }
                        function show(backend) {
                            if (workspace.sessionId.length === 0 || backend === workspace.backend) {
                                viewedBackend = ""
                                workspace.setBackend(backend)
                            } else {
                                viewedBackend = backend
                                workspace.discover(backend)
                            }
                        }
                        width: modelsScroll.availableWidth; spacing: 18
                        RowLayout {
                            Layout.fillWidth: true; Layout.leftMargin: modelsPage.sideMargin; Layout.rightMargin: modelsPage.sideMargin; Layout.topMargin: 30
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 6
                                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Il modello giusto, scelto da te."); color: appearance.colors.text; font.pixelSize: appearance.uiFont.pixelSize + 11; font.weight: Font.DemiBold; font.letterSpacing: -0.2; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; textFormat: Text.PlainText; text: qsTr("La scelta riguarda Kira. La configurazione globale di Codex resta tua."); color: appearance.colors.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            }
                            KButton { objectName: "modelsRefreshButton"; text: qsTr("Aggiorna"); iconName: "refresh"; onClicked: workspace.discover(modelsPage.shownBackend); toolTipText: qsTr("Aggiorna il catalogo del provider selezionato") }
                        }
                        // Providers are a segmented row; the selected one carries the page.
                        Flow {
                            Layout.fillWidth: true; Layout.leftMargin: modelsPage.sideMargin; Layout.rightMargin: modelsPage.sideMargin; spacing: 6
                            KButton { objectName: "ollamaBackendButton"; text: "Ollama"; cornerRadius: 17; enabled: !workspace.busy && !assistant.active; checked: modelsPage.shownBackend === "ollama"; onClicked: modelsPage.show("ollama") }
                            KButton { objectName: "codexBackendButton"; text: "Codex"; cornerRadius: 17; enabled: !workspace.busy && !assistant.active; checked: modelsPage.shownBackend === "codex"; onClicked: modelsPage.show("codex") }
                            KButton { objectName: "directBackendButton"; text: qsTr("OpenAI diretto · ChatGPT"); cornerRadius: 17; enabled: !workspace.busy && !assistant.active; checked: modelsPage.shownBackend === "openai_chatgpt"; onClicked: modelsPage.show("openai_chatgpt") }
                            KButton { objectName: "deepseekBackendButton"; text: qsTr("DeepSeek Account"); cornerRadius: 17; enabled: !workspace.busy && !assistant.active; checked: modelsPage.shownBackend === "deepseek_account"; onClicked: modelsPage.show("deepseek_account") }
                            KButton { objectName: "deepseekWebBackendButton"; text: qsTr("DeepSeek Web · sperimentale"); cornerRadius: 17; enabled: !workspace.busy && !assistant.active; checked: modelsPage.shownBackend === "deepseek_web"; onClicked: modelsPage.show("deepseek_web") }
                            KButton { objectName: "claudeCodeBackendButton"; text: qsTr("Claude Code · uso personale"); cornerRadius: 17; enabled: !workspace.busy && !assistant.active; checked: modelsPage.shownBackend === "claude_code"; onClicked: modelsPage.show("claude_code") }
                        }
                        // One status line: what the provider reported and the one action that follows.
                        Rectangle {
                            objectName: "providerStatusCard"
                            Layout.fillWidth: true; Layout.leftMargin: modelsPage.sideMargin; Layout.rightMargin: modelsPage.sideMargin
                            implicitHeight: providerStatusRow.implicitHeight + 24; radius: 12
                            color: appearance.colors.surface; border.color: appearance.colors.border
                            RowLayout {
                                id: providerStatusRow; anchors.fill: parent; anchors.margins: 12; anchors.leftMargin: 16; spacing: 12
                                Rectangle { objectName: "providerStatusDot"; width: 10; height: 10; radius: 5; color: modelsPage.foreign ? (modelsPage.viewedCatalog.available ? appearance.colors.success : modelsPage.viewedCatalog.error ? appearance.colors.warning : muted) : workspace.providerState === "error" ? appearance.colors.danger : workspace.providerState === "ok" ? appearance.colors.success : workspace.providerState === "warning" ? appearance.colors.warning : muted; Layout.alignment: Qt.AlignVCenter }
                                ReadText { objectName: "providerStatusText"; text: !modelsPage.foreign ? workspace.providerText : modelsPage.viewedCatalog.available ? qsTr("%1 modelli disponibili. Questa chat usa %2: con «Usa modello» la continui con %3.").arg((modelsPage.viewedCatalog.models || []).length).arg(kformat.providerLabel(workspace.backend)).arg(kformat.providerLabel(modelsPage.viewedBackend)) : modelsPage.viewedCatalog.error ? qsTr("%1 non è ancora pronto: %2").arg(kformat.providerLabel(modelsPage.viewedBackend)).arg(modelsPage.viewedCatalog.error) : qsTr("Caricamento dei modelli…"); Layout.fillWidth: true; color: !modelsPage.foreign && workspace.providerUnavailable ? appearance.colors.danger : appearance.colors.text; wrapMode: Text.Wrap }
                                KButton { objectName: "codexLoginButton"; text: qsTr("Accedi con Codex"); iconName: "external"; visible: modelsPage.shownBackend === "codex"; onClicked: workspace.login() }
                            }
                        }
                        DirectAccountPane { objectPrefix: "modelsChatGPT"; visible: modelsPage.shownBackend === "openai_chatgpt"; Layout.leftMargin: modelsPage.sideMargin; Layout.rightMargin: modelsPage.sideMargin }
                        DeepSeekAccountPane { visible: modelsPage.shownBackend === "deepseek_account"; Layout.leftMargin: modelsPage.sideMargin; Layout.rightMargin: modelsPage.sideMargin }
                        DeepSeekWebPane { objectName: "deepseekWebPane"; visible: modelsPage.shownBackend === "deepseek_web"; Layout.leftMargin: modelsPage.sideMargin; Layout.rightMargin: modelsPage.sideMargin }
                        ReadText { objectName: "claudeCodePersonalNotice"; visible: modelsPage.shownBackend === "claude_code"; text: qsTr("Claude Code usa l'accesso già fatto nel tuo Claude Code locale: Kira non legge né conserva le credenziali. Solo per uso personale: non distribuire Kira con questo provider. I permessi del progetto decidono gli strumenti di Claude Code; Kira blocca i percorsi fuori dal progetto e registra le azioni osservate."); Layout.fillWidth: true; Layout.leftMargin: modelsPage.sideMargin; Layout.rightMargin: modelsPage.sideMargin; color: muted; wrapMode: Text.Wrap }
                        GridLayout {
                            Layout.fillWidth: true; Layout.leftMargin: modelsPage.sideMargin; Layout.rightMargin: modelsPage.sideMargin
                            columns: modelsScroll.availableWidth - modelsPage.sideMargin * 2 < 700 ? 1 : 2; columnSpacing: 14; rowSpacing: 14
                            Repeater {
                                model: modelsPage.foreign ? (modelsPage.viewedCatalog.models || []) : workspace.models
                                delegate: Rectangle {
                                    id: modelCard
                                    required property var modelData
                                    objectName: "modelCard_" + modelData.id
                                    readonly property bool selected: !modelsPage.foreign && workspace.selectedModel === modelData.id
                                    readonly property var efforts: (modelData.supported_reasoning_efforts || []).map(function(value) { return typeof value === "string" ? value : String(value.reasoningEffort || value.reasoning_effort || "") })
                                    // Cards in one row share their height; the action stays at the bottom.
                                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 0
                                    implicitHeight: modelColumn.implicitHeight + 32; radius: 14
                                    color: appearance.colors.surface
                                    border.width: selected ? 2 : 1
                                    border.color: selected ? appearance.colors.accent : appearance.colors.border
                                    ColumnLayout {
                                        id: modelColumn; anchors.fill: parent; anchors.margins: 16; spacing: 6
                                        RowLayout {
                                            Layout.fillWidth: true; spacing: 10
                                            Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: modelCard.modelData.name || modelCard.modelData.id; color: appearance.colors.text; font.pixelSize: appearance.uiFont.pixelSize + 4; font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
                                            Text { font.family: appearance.codeFont.family; textFormat: Text.PlainText; text: modelCard.modelData.id; color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2); elide: Text.ElideMiddle; Layout.maximumWidth: 200 }
                                        }
                                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: modelsPage.shownBackend === "claude_code" ? qsTr("Agente Claude Code · azioni native osservate da Kira") : modelsPage.shownBackend === "deepseek_web" ? qsTr("Strumenti mediati da Kira · protocollo sperimentale") : modelCard.modelData.tools_verified ? qsTr("Strumenti verificati") : modelCard.modelData.tools_metadata ? qsTr("Supporto strumenti dichiarato · verifica al primo uso") : modelsPage.shownBackend === "codex" ? qsTr("Agente Codex · l'accesso va confermato con un turno reale") : ["openai_chatgpt", "deepseek_account"].indexOf(modelsPage.shownBackend) >= 0 ? qsTr("Strumenti gestiti da Kira · verifica al primo uso") : qsTr("Conversazione"); color: appearance.colors.textSecondary; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1); Layout.fillWidth: true; wrapMode: Text.Wrap }
                                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; objectName: "modelSize_" + modelCard.modelData.id; visible: !!modelCard.modelData.size_bytes || modelsPage.shownBackend === "ollama"; text: modelCard.modelData.size_bytes ? qsTr("Dimensione modello: %1 GiB").arg(Number(modelCard.modelData.size_bytes / 1073741824).toLocaleString(Qt.locale(localization.language), "f", 2)) : qsTr("Dimensione / VRAM: consulta i dati disponibili sotto"); color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); Layout.fillWidth: true; wrapMode: Text.Wrap }
                                        Flow {
                                            visible: modelCard.efforts.length > 0
                                            Layout.fillWidth: true; spacing: 4
                                            Repeater {
                                                model: modelCard.efforts
                                                delegate: Rectangle {
                                                    required property string modelData
                                                    implicitWidth: effortLabel.implicitWidth + 14; implicitHeight: 20; radius: 10
                                                    color: workspace.selectedEffort === modelData && modelCard.selected ? appearance.colors.accentSoft : appearance.colors.surfaceAlt
                                                    Text { id: effortLabel; anchors.centerIn: parent; text: parent.modelData; textFormat: Text.PlainText; font.family: appearance.codeFont.family; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); color: appearance.colors.textSecondary }
                                                }
                                            }
                                        }
                                        Item { Layout.fillHeight: true }
                                        RowLayout {
                                            Layout.fillWidth: true; Layout.topMargin: 4
                                            Item { Layout.fillWidth: true }
                                            Text { visible: modelCard.selected; font.family: appearance.uiFont.family; textFormat: Text.PlainText; text: qsTr("Selezionato"); color: appearance.colors.accent; font.weight: Font.DemiBold; font.pixelSize: appearance.uiFont.pixelSize }
                                            KButton { objectName: "useModel_" + modelCard.modelData.id; visible: !modelCard.selected; text: qsTr("Usa modello"); enabled: !workspace.busy && !assistant.active; onClicked: { if (modelsPage.foreign) { providerSwitch.chooseModel(modelsPage.viewedBackend, modelCard.modelData.id); return } workspace.setModel(modelCard.modelData.id); if (hasProject) window.page = 0 } }
                                        }
                                    }
                                }
                            }
                        }
                        // Raw provider data stay available, folded by default.
                        Rectangle {
                            visible: !modelsPage.foreign
                            Layout.fillWidth: true; Layout.leftMargin: modelsPage.sideMargin; Layout.rightMargin: modelsPage.sideMargin; Layout.bottomMargin: 30
                            implicitHeight: catalogColumn.implicitHeight + 2; radius: 12
                            color: appearance.colors.surface; border.color: appearance.colors.border
                            ColumnLayout {
                                id: catalogColumn; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; spacing: 0
                                KButton {
                                    id: catalogToggle; objectName: "catalogToggle"
                                    Layout.fillWidth: true; implicitHeight: 42
                                    text: qsTr("Dati del provider · valori non disponibili restano sconosciuti"); iconName: checked ? "chevron-down" : "chevron-right"
                                    subtle: true; quietSelection: true; leftAligned: true; checkable: true; checked: false
                                    Accessible.name: checked ? qsTr("Nascondi i dati del provider") : qsTr("Mostra i dati del provider")
                                }
                                ReadText { objectName: "catalogText"; visible: catalogToggle.checked; color: appearance.colors.codeText; text: workspace.catalogText; Layout.fillWidth: true; Layout.leftMargin: 16; Layout.rightMargin: 16; Layout.bottomMargin: 16; font.family: appearance.codeFont.family; font.styleName: appearance.codeFont.styleName; font.weight: appearance.codeFont.weight; font.italic: appearance.codeFont.italic; font.pixelSize: appearance.codeFont.pixelSize - 1 }
                            }
                        }
                    }
                }
                ColumnLayout {
                    spacing: 18
                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: hasProject ? qsTr("Esecuzioni di %1").arg(workspace.project.name) : qsTr("Apri un progetto per vedere le esecuzioni."); color: appearance.colors.text; font.pixelSize: appearance.uiFont.pixelSize + 5; Layout.fillWidth: true; Layout.margins: 28; wrapMode: Text.WordWrap }
                    ListView {
                        id: activityList
                        Layout.fillWidth: true; Layout.fillHeight: true; Layout.leftMargin: 28; Layout.rightMargin: 28
                        clip: true; spacing: 12; model: workspace.activity
                        ScrollBar.vertical: ScrollBar {}
                        delegate: Rectangle {
                            required property var modelData
                            width: activityList.width; implicitHeight: activityColumn.implicitHeight + 26
                            color: appearance.colors.surfaceAlt; radius: 9; border.color: appearance.colors.border
                            ColumnLayout {
                                id: activityColumn; anchors.fill: parent; anchors.margins: 13; spacing: 9
                                RowLayout { Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; textFormat: Text.PlainText; text: modelData.label; color: accent; font.weight: Font.Medium; Layout.fillWidth: true } Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; objectName: "activityStarted_" + modelData.id; text: kformat.timestamp(modelData.created_at); color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3) } }
                                ReadText { text: modelData.requestExcerpt || modelData.prompt || ""; Layout.fillWidth: true }
                                RowLayout {
                                    KButton { text: qsTr("Apri chat"); subtle: true; onClicked: { workspace.selectSession(modelData.session_id); window.page = 0 } }
                                    KButton { text: qsTr("Dettagli"); subtle: true; onClicked: { window.eventDetails = modelData.details || ""; window.eventDetailsIsRun = true; eventDialog.open() } }
                                }
                                ReadText { text: modelData.error || ""; visible: text.length > 0; color: appearance.colors.danger; Layout.fillWidth: true }
                                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Esecuzione %1").arg(modelData.id); color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); Layout.fillWidth: true; elide: Text.ElideMiddle }
                            }
                        }
                    }
                }
                SettingsPane {
                    onDiagnosticsRequested: diagnosticsDialog.open()
                    onSoulsRequested: soulDialog.open()
                    onUpdateRequested: updateDialog.open()
                }
                Loader {
                    objectName: "teamPageLoader"
                    active: window.page === 4
                    source: "TeamView.qml"
                }
            }
        }
    }

    Component {
        id: projectDetails
                            ScrollView {
                                anchors.fill: parent; anchors.margins: 22; clip: true; contentWidth: availableWidth
                                ColumnLayout {
                                    width: parent.width; spacing: 16
                                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("NEL PROGETTO"); color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 4); font.letterSpacing: 1.5 }
                                    ReadText { text: workspace.project.root || ""; Layout.fillWidth: true; color: appearance.colors.text; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
                                    KButton { text: qsTr("Apri cartella  ↗"); subtle: true; onClicked: workspace.openProjectFolder() }
                                    Rectangle { Layout.fillWidth: true; height: 1; color: appearance.colors.border }
                                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("MODELLO & CAPACITÀ"); color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 4); font.letterSpacing: 1.3 }
                                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: workspace.selectedModel || qsTr("Da selezionare"); color: accent; font.pixelSize: appearance.uiFont.pixelSize + 1; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                    ReadText { text: workspace.capabilitiesText; Layout.fillWidth: true; color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
                                    RowLayout {
                                        visible: workspace.backend === "ollama"
                                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Contesto richiesto"); color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); Layout.fillWidth: true }
                                        KComboBox {
                                            objectName: "ollamaContextPreset"; Layout.preferredWidth: 150
                                            model: [qsTr("Predefinito Ollama"), qsTr("32K"), qsTr("128K"), qsTr("256K")]
                                            currentIndex: [0, 32768, 131072, 262144].indexOf(workspace.requestedContext)
                                            enabled: !workspace.busy && !assistant.active
                                            onActivated: workspace.setContextSize([0, 32768, 131072, 262144][currentIndex])
                                            Accessible.name: qsTr("Dimensione contesto Ollama")
                                        }
                                    }
                                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Il contesto effettivo dipende dal modello e dalla memoria disponibile. 128K e 256K possono richiedere molta RAM/VRAM; verifica i dati del modello in Ollama."); visible: workspace.backend === "ollama"; color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                    RowLayout {
                                        visible: workspace.backend === "ollama"
                                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Ragionamento interno del modello"); color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); Layout.fillWidth: true }
                                        KComboBox {
                                            objectName: "ollamaThinkMode"; Layout.preferredWidth: 190
                                            model: [qsTr("Predefinito del modello"), qsTr("Ragionamento interno disattivato"), qsTr("Ragionamento interno attivato")]
                                            currentIndex: workspace.requestedThink
                                            enabled: !workspace.busy && !assistant.active
                                            onActivated: workspace.setThink(currentIndex)
                                            Accessible.name: qsTr("Modalità di ragionamento Ollama")
                                        }
                                    }
                                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Il ragionamento interno non viene mostrato in Kira. Disattivarlo può lasciare più spazio alla risposta nella finestra del modello."); visible: workspace.backend === "ollama"; color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: workspace.backend !== "ollama" ? qsTr("Il catalogo non prova l'accesso: lo conferma un turno riuscito.") : qsTr("Gli strumenti dichiarati vengono verificati prima del primo utilizzo."); color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                    Rectangle { Layout.fillWidth: true; height: 1; color: appearance.colors.border }
                                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("ARTEFATTI"); color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 4); font.letterSpacing: 1.3 }
                                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("I risultati compariranno qui."); visible: workspace.artifacts.length === 0; color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2); Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                    Repeater {
                                        model: workspace.artifacts
                                        delegate: KButton { required property var modelData; Layout.fillWidth: true; text: "↗  " + String(modelData.path).split("/").pop(); onClicked: workspace.openArtifact(modelData.path); toolTipText: modelData.path }
                                    }
                                    Rectangle { Layout.fillWidth: true; height: 1; color: appearance.colors.border }
                                    KButton { text: qsTr("Contesto fornito da Kira"); Layout.fillWidth: true; onClicked: contextDialog.open() }
                                    KButton { text: qsTr("Uso e risorse misurati"); Layout.fillWidth: true; onClicked: metricsDialog.open() }
                                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Il lavoro attivo continua alla chiusura della finestra. Arresto e sospensione del sistema sono eventi distinti."); color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); Layout.fillWidth: true; wrapMode: Text.WordWrap; lineHeight: 1.35; Layout.topMargin: 12 }
                                    Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Vista limitata a 60 esecuzioni e 600 righe. Esporta per la cronologia completa."); color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                }
                            }
    }
    Drawer {
        id: detailsDrawer; objectName: "detailsDrawer"
        edge: Qt.RightEdge; width: Math.min(340, window.width - 48); height: window.height
        modal: true; interactive: true
        onClosed: if (window.detailsDrawerRequested) workspace.setUiPreference("details_visible", false)
        background: Rectangle { color: appearance.colors.surface; border.color: appearance.colors.border }
        ColumnLayout {
            anchors.fill: parent; spacing: 0
            KButton { objectName: "detailsDrawerClose"; text: qsTr("Chiudi dettagli"); subtle: true; Layout.alignment: Qt.AlignRight; Layout.margins: 10; onClicked: detailsDrawer.close() }
            Loader { Layout.fillWidth: true; Layout.fillHeight: true; sourceComponent: projectDetails }
        }
    }

    Loader {
        id: inputDialog
        parent: Overlay.overlay
        anchors.fill: parent
        active: inputs.requests.length > 0
        source: "InputDialog.qml"
        function open() { active = true; if (item) item.open() }
        onLoaded: if (inputs.pendingCount > 0) item.open()
    }
    ActionPalette { id: actionPalette; parent: Overlay.overlay; onSelected: function(action) { window.chooseAction(action) }; onClosed: window.commandStart = -1 }
    ProjectFileDialog { id: projectFileDialog; parent: Overlay.overlay; onClosed: window.mentionStart = -1 }
    // Covers the window, popups included, until the startup stages are done.
    KiraLoadingScreen { parent: Overlay.overlay }
    UpdateDialog { id: updateDialog }
    WorkbenchDialog { id: workbenchDialog; parent: Overlay.overlay }
    KiraFileDialog { id: chatMarkdownExport; mode: "save"; purpose: "export"; title: qsTr("Esporta chat"); nameFilters: [qsTr("Markdown (*.md)")]; defaultSuffix: "md"; suggestedName: window.exportName(qsTr("chat")); onAccepted: workbench.exportChat(selectedFile.toString()) }
    WorkspaceDialog { id: resumeDialog; parent: Overlay.overlay; inspectorKey: "riprendi" }
    ActionDialog { id: actionDialog; parent: Overlay.overlay; onProjectSelected: { window.page = 0; window.followOutput = true } }
    DiagnosticsDialog { id: diagnosticsDialog; parent: Overlay.overlay }
    ProviderSwitchDialog {
        id: providerSwitchDialog; parent: Overlay.overlay
        // A provider without access yet: show its setup on the models page.
        onConfigureRequested: function(backend) { window.page = 1; modelsPage.viewedBackend = backend; workspace.discover(backend) }
    }
    // The guide only navigates: provider, model and account stay the user's choice.
    OnboardingDialog { id: onboardingDialog; parent: Overlay.overlay; onCreateRequested: newProject.open(); onModelsRequested: window.page = 1 }

    KiraFileDialog {
        id: folderDialog; objectName: "projectFolderDialog"; mode: "folder"; purpose: "project"
        title: qsTr("Scegli la cartella del progetto")
        subtitle: qsTr("Kira lavorerà in questa cartella con i permessi che scegli per il progetto.")
        currentFolder: rootInput.text
        onAccepted: rootInput.text = selectedPath
    }
    // The header exports the selected chat as Markdown; project memory stays JSON.
    KiraFileDialog {
        id: exportDialog; objectName: "projectExportDialog"; title: window.memoryExport ? qsTr("Esporta memoria") : qsTr("Esporta chat")
        mode: "save"; purpose: "export"
        suggestedName: window.exportName(window.memoryExport ? qsTr("memoria") : qsTr("chat"))
        nameFilters: window.memoryExport ? [qsTr("JSON (*.json)"), qsTr("Testo (*.txt)"), qsTr("Tutti i file (*)")] : [qsTr("Markdown (*.md)")]
        defaultSuffix: window.memoryExport ? "json" : "md"
        onAccepted: { if (window.memoryExport) workspace.exportMemory(selectedFile.toString()); else workspace.exportTranscript(selectedFile.toString()) }
    }
    KiraSoulsDialog { id: soulDialog; inspectorKey: "anima" }
    Dialog {
        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
        id: newProject; objectName: "newProjectDialog"; title: qsTr("Un nuovo spazio di lavoro"); modal: true
        anchors.centerIn: parent; width: Math.min(590, window.width - 60)
        standardButtons: Dialog.NoButton
        onOpened: permissionInput.currentIndex = Math.max(0, ["read_only", "project_write", "trusted_host", "full_access"].indexOf(workspace.uiPreferences.default_permission))
        background: Rectangle { color: appearance.colors.surfaceAlt; radius: 12; border.color: appearance.colors.border }
        ColumnLayout {
            width: parent.width; spacing: 14
            Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; textFormat: Text.PlainText; text: qsTr("Il progetto usa la cartella che scegli. I permessi valgono per le operazioni successive."); color: muted; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            TextField { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; id: nameInput; objectName: "projectNameInput"; placeholderText: qsTr("Nome del progetto"); Layout.fillWidth: true; selectByMouse: true }
            RowLayout {
                TextField { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; id: rootInput; objectName: "projectRootInput"; placeholderText: qsTr("Cartella del progetto"); Layout.fillWidth: true; selectByMouse: true }
                KButton { text: qsTr("Sfoglia…"); onClicked: folderDialog.open() }
            }
            TextArea { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; textFormat: TextEdit.PlainText; id: instructionsInput; placeholderText: qsTr("Istruzioni del progetto (opzionali)"); Layout.fillWidth: true; Layout.preferredHeight: 86; wrapMode: TextEdit.Wrap; selectByMouse: true }
            KComboBox {
                palette.dark: window.muted
                id: permissionInput; objectName: "newProjectPermission"; Layout.fillWidth: true
                model: [qsTr("Sola lettura"), qsTr("Scrittura nel progetto"), qsTr("Host fidato · comandi come il tuo utente"), qsTr("Accesso completo")]
            }
            Text { objectName: "newProjectPermissionDescription"; font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: permissionInput.currentIndex === 3 ? qsTr("Consenti lettura e scrittura anche fuori dal progetto e comandi come il tuo utente. Restano i limiti del sistema operativo, senza privilegi amministrativi automatici.") : permissionInput.currentIndex === 2 ? qsTr("Autorizzi i comandi sul computer con i tuoi permessi. Gli strumenti file restano nel progetto; i comandi non sono in una sandbox.") : permissionInput.currentIndex === 1 ? qsTr("Consenti lettura e scrittura dei file del progetto. Codex usa la propria sandbox workspace; i comandi Kira richiedono Host fidato o Accesso completo.") : qsTr("Consenti la lettura dei file. Modifiche e comandi Kira vengono rifiutati dal servizio."); color: permissionInput.currentIndex >= 2 ? appearance.colors.warning : muted; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
            RowLayout {
                Layout.topMargin: 8; Item { Layout.fillWidth: true }
                KButton { text: qsTr("Annulla"); subtle: true; onClicked: newProject.close() }
                KButton { objectName: "createProjectButton"; text: qsTr("Crea progetto"); primary: true; enabled: workspace.connected && nameInput.text.trim().length > 0 && rootInput.text.length > 0; onClicked: workspace.createProject(nameInput.text, rootInput.text, instructionsInput.text, ["read_only", "project_write", "trusted_host", "full_access"][permissionInput.currentIndex]) }
            }
        }
    }
    Dialog {
        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
        id: memoryDialog; objectName: "projectPermissionDialog"; title: qsTr("Memoria e istruzioni del progetto"); modal: true
        anchors.centerIn: parent; width: Math.min(780, window.width - 60); height: Math.min(720, window.height - 70)
        background: Rectangle { color: appearance.colors.surfaceAlt; radius: 12; border.color: appearance.colors.border }
        onOpened: { memoryInput.text = workspace.memoryText; editInstructions.text = workspace.project.instructions || ""; editPermission.currentIndex = ["read_only", "project_write", "trusted_host", "full_access"].indexOf(workspace.project.permission) }
        ColumnLayout {
            anchors.fill: parent; spacing: 12
            Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Separa obiettivo, decisioni verificate, ipotesi, domande e prossimo passo. Collega le conclusioni a eventi, artefatti o hash; il testo qui è modificabile."); color: muted; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1) }
            ScrollView { Layout.fillWidth: true; Layout.preferredHeight: 80; clip: true; TextArea { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; textFormat: TextEdit.PlainText; id: editInstructions; placeholderText: qsTr("Istruzioni"); wrapMode: TextEdit.Wrap; selectByMouse: true } }
            KComboBox { palette.dark: window.muted; id: editPermission; objectName: "projectPermissionChoice"; Layout.fillWidth: true; model: [qsTr("Sola lettura"), qsTr("Scrittura nel progetto"), qsTr("Host fidato · nessuna sandbox per i comandi Kira"), qsTr("Accesso completo")]; enabled: !workspace.busy && !assistant.active && !teams.busy; Accessible.name: qsTr("Permessi del progetto") }
            Text { objectName: "projectPermissionDescription"; font: appearance.uiFont; textFormat: Text.PlainText; text: editPermission.currentIndex === 3 ? qsTr("Consenti lettura e scrittura anche fuori dal progetto e comandi come il tuo utente. Restano i limiti del sistema operativo, senza privilegi amministrativi automatici.") : editPermission.currentIndex === 2 ? qsTr("Autorizzi i comandi sul computer con i tuoi permessi. Gli strumenti file restano nel progetto; i comandi non sono in una sandbox.") : editPermission.currentIndex === 1 ? qsTr("Consenti lettura e scrittura dei file del progetto. Codex usa la propria sandbox workspace; i comandi Kira richiedono Host fidato o Accesso completo.") : qsTr("Consenti la lettura dei file. Modifiche e comandi Kira vengono rifiutati dal servizio."); color: editPermission.currentIndex >= 2 ? appearance.colors.warning : muted; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; TextArea { color: appearance.colors.codeText; id: memoryInput; objectName: "memoryInput"; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; selectByMouse: true; font.family: appearance.codeFont.family; font.styleName: appearance.codeFont.styleName; font.weight: appearance.codeFont.weight; font.italic: appearance.codeFont.italic; font.pixelSize: appearance.codeFont.pixelSize } }
            RowLayout {
                KButton { text: qsTr("Esporta JSON"); onClicked: { window.memoryExport = true; exportDialog.open() } }
                KButton { text: qsTr("Contesto inviato"); onClicked: contextDialog.open() }
                Item { Layout.fillWidth: true }
                KButton { text: qsTr("Chiudi"); subtle: true; onClicked: memoryDialog.close() }
                KButton { objectName: "projectPermissionSave"; text: qsTr("Salva"); primary: true; enabled: workspace.connected; onClicked: workspace.updateProject(editInstructions.text, ["read_only", "project_write", "trusted_host", "full_access"][editPermission.currentIndex], memoryInput.text) }
            }
        }
    }
    KiraPanel {
        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
        id: contextDialog; title: qsTr("Contesto fornito da Kira")
        standardButtons: Dialog.Close
        ColumnLayout {
            anchors.fill: parent
            Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Questo è il contributo effettivamente inviato da Kira nell'ultima esecuzione. Il prompt interno del provider può contenere altro contesto non osservabile."); color: muted; wrapMode: Text.WordWrap; Layout.fillWidth: true; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1) }
            ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; ReadText { text: workspace.contextText; width: parent.width } }
            KButton { text: qsTr("Copia contesto"); onClicked: workspace.copy(workspace.contextText) }
        }
    }
    KiraPanel {
        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
        id: eventDialog; objectName: "eventDialog"; title: window.eventDetailsTitle
        standardButtons: Dialog.Close
        ScrollView { anchors.fill: parent; clip: true; ReadText { color: appearance.colors.codeText; objectName: "eventDetailsText"; text: window.eventDetails; width: parent.width; font.family: appearance.codeFont.family; font.styleName: appearance.codeFont.styleName; font.weight: appearance.codeFont.weight; font.italic: appearance.codeFont.italic; font.pixelSize: appearance.codeFont.pixelSize } }
    }
    KiraPanel {
        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
        id: artifactsDialog; objectName: "artifactsDialog"; title: qsTr("Artefatti del progetto"); inspectorKey: "file"
        standardButtons: Dialog.Close
        ListView {
            id: artifactList; anchors.fill: parent; clip: true; spacing: 10; model: workspace.artifacts
            ScrollBar.vertical: ScrollBar {}
            delegate: KButton { required property var modelData; width: artifactList.width; text: "↗  " + String(modelData.path).split("/").pop(); onClicked: workspace.openArtifact(modelData.path); toolTipText: modelData.path }
            Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; textFormat: Text.PlainText; text: qsTr("Nessun artefatto registrato."); width: artifactList.width; wrapMode: Text.Wrap; visible: artifactList.count === 0; color: muted }
        }
    }
    UsageDialog { id: metricsDialog; inspectorKey: "uso"; onContextRequested: chatContextDialog.open() }
    ContextDialog { id: chatContextDialog; inspectorKey: "contesto" }
    ResearchDialog { id: researchDialog; inspectorKey: "ricerca" }
    WebDialog { id: webDialog }
    ChatGPTWelcomeDialog { parent: Overlay.overlay }
    Dialog {
        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
        id: renameChatDialog; objectName: "renameChatDialog"
        property string chatId: ""
        title: qsTr("Rinomina chat"); modal: true; anchors.centerIn: parent
        width: Math.min(480, window.width - 40)
        background: Rectangle { color: appearance.colors.surface; radius: 12; border.color: appearance.colors.border }
        function save() {
            if (chatNameInput.text.trim().length === 0) return
            workspace.renameSession(chatId, chatNameInput.text)
            close()
        }
        onOpened: { chatNameInput.forceActiveFocus(); chatNameInput.selectAll() }
        contentItem: ColumnLayout {
            TextField { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; id: chatNameInput; objectName: "chatNameInput"; Layout.fillWidth: true; maximumLength: 120; selectByMouse: true; placeholderText: qsTr("Nome della chat"); Accessible.name: qsTr("Nome della chat"); onAccepted: renameChatDialog.save() }
            RowLayout {
                Item { Layout.fillWidth: true }
                KButton { text: qsTr("Annulla"); onClicked: renameChatDialog.close() }
                KButton { objectName: "saveChatName"; text: qsTr("Salva"); primary: true; enabled: chatNameInput.text.trim().length > 0; onClicked: renameChatDialog.save() }
            }
        }
    }
    KiraPanel {
        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
        id: sharedMemoryDialog; objectName: "sharedMemoryDialog"; inspectorKey: "memoria"
        title: qsTr("Memoria del progetto")
        onOpened: workspace.refreshSharedContext()
        contentItem: ColumnLayout {
            spacing: 12
            Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; text: qsTr("Le chat hanno cronologie separate. Kira condivide le note di questo progetto e brevi estratti recenti delle sue altre chat."); textFormat: Text.PlainText; wrapMode: Text.Wrap; color: appearance.colors.text; Layout.fillWidth: true }
            Switch { HoverHandler { cursorShape: appearance.pointerCursors && parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }  font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; objectName: "shareChatsSwitch"; id: shareChatControl; Layout.fillWidth: true; text: qsTr("Usa estratti delle altre chat di questo progetto"); contentItem: Text { text: shareChatControl.text; font: appearance.uiFont; color: appearance.colors.text; textFormat: Text.PlainText; wrapMode: Text.Wrap; leftPadding: shareChatControl.indicator.width + 10; verticalAlignment: Text.AlignVCenter } checked: workspace.sharedSessionMemoryEnabled; onToggled: workspace.setSharedSessionMemory(checked) }
            Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; text: qsTr("Massimo 5 chat e 6.000 caratteri di estratti. Le risposte precedenti non sono fatti verificati. Disattivare la condivisione evita nuovi invii, ma non rimuove ciò che il provider ha già ricevuto."); textFormat: Text.PlainText; wrapMode: Text.Wrap; color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2); Layout.fillWidth: true }
            ScrollView {
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                ReadText { objectName: "sharedMemoryText"; width: parent.width; text: workspace.sharedContextLoading ? qsTr("Caricamento…") : workspace.sharedContextText; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1) }
            }
            RowLayout {
                KButton { text: qsTr("Modifica note del progetto"); onClicked: { sharedMemoryDialog.close(); memoryDialog.open() } }
                Item { Layout.fillWidth: true }
                KButton { text: qsTr("Chiudi"); onClicked: sharedMemoryDialog.close() }
            }
        }
    }
    Dialog {
        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
        // Codex approvals and Kira's own browser consents share this dialog.
        id: approvalDialog; objectName: "approvalDialog"; title: qsTr("Decisione richiesta"); modal: true
        anchors.centerIn: parent; width: Math.min(690, window.width - 60); height: Math.min(530, window.height - 80)
        visible: Boolean(workspace.approval.approval_id) && workspace.approval.kind !== "browser_manual_login"
        closePolicy: Popup.NoAutoClose
        background: Rectangle { color: appearance.colors.surfaceAlt; radius: 12; border.color: appearance.colors.warning }
        ColumnLayout {
            anchors.fill: parent; spacing: 14
            ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; ReadText { color: appearance.colors.codeText; font.pixelSize: appearance.codeFont.pixelSize; text: workspace.approval.description || ""; width: parent.width; font.family: appearance.codeFont.family; font.styleName: appearance.codeFont.styleName; font.weight: appearance.codeFont.weight; font.italic: appearance.codeFont.italic } }
            ReadText {
                objectName: "approvalAlwaysHint"
                visible: workspace.approvalCanAlways
                text: qsTr("Potrai revocare il consenso nelle impostazioni web della chat.")
                Layout.fillWidth: true; color: appearance.colors.muted
            }
            Flow {
                objectName: "approvalActions"
                Layout.fillWidth: true; spacing: 8; layoutDirection: Qt.RightToLeft
                KButton { objectName: "approvalAllowOnce"; text: qsTr("Consenti una volta"); primary: true; enabled: !workspace.approvalBusy && !workspace.web.busy; onClicked: workspace.approve("accept") }
                KButton { objectName: "approvalAllowAlways"; text: qsTr("Consenti sempre in questa chat"); visible: workspace.approvalCanAlways; enabled: !workspace.approvalBusy && !workspace.web.busy; onClicked: workspace.approve("accept_always") }
                KButton { objectName: "approvalDecline"; text: qsTr("Rifiuta"); enabled: !workspace.approvalBusy && !workspace.web.busy; onClicked: workspace.approve("decline") }
            }
        }
    }
    Dialog {
        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
        id: closeDialog; objectName: "closeConfirmation"; title: (workspace.busy || teams.busy || assistant.active) ? qsTr("Il lavoro continua nel servizio") : qsTr("Chiudere Kira Desktop?"); modal: true
        anchors.centerIn: parent; width: 470
        background: Rectangle { color: appearance.colors.surfaceAlt; radius: 12; border.color: appearance.colors.border }
        ColumnLayout {
            width: parent.width; spacing: 18
            Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; textFormat: Text.PlainText; text: (workspace.busy || teams.busy || assistant.active) ? qsTr("Puoi chiudere la finestra e riconnetterti alla cronologia. Per interrompere il lavoro usa Ferma. Spegnere il computer o arrestare WSL interrompe anche il servizio.") : qsTr("La cronologia è conservata. Chiudere questa finestra lascia disponibile il servizio locale."); color: muted; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            RowLayout { Item { Layout.fillWidth: true } KButton { text: qsTr("Resta"); onClicked: closeDialog.close() } KButton { text: qsTr("Chiudi finestra"); primary: true; onClicked: { window.closeConfirmed = true; window.close() } } }
        }
    }
}
