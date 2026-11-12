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
    readonly property bool canvasFocused: workspace.canvas.paneVisible && (workspace.canvas.expanded || window.width < 1120)
    readonly property bool visualFocused: browserFocused || studioFocused || canvasFocused
    readonly property bool motionEnabled: !appearance.reduceMotion && window.visible && window.visibility !== Window.Minimized
    readonly property bool chatMotionEnabled: motionEnabled && page === 0
    readonly property var chatIndicators: workspace.chatStatusIndicators
    readonly property bool browserPresented: window.visible && window.visibility !== Window.Minimized && page === 0
    onBrowserPresentedChanged: workspace.web.setWindowActive(browserPresented)
    readonly property bool detailsVisible: workspace.uiPreferences.details_visible
    readonly property bool detailsDrawerRequested: detailsVisible && width < 1120 && hasProject && page === 0 && !workspace.web.paneVisible && !workspace.studio.paneVisible && !workspace.canvas.paneVisible
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
        else if (action === "research") { window.page = 0; researchDialog.open() }
        else if (action === "editor") { window.page = 0; workspace.web.hideBrowser(); workspace.studio.hideStudio(); workspace.canvas.openProjectExplorer() }
        else if (action === "checkpoint") { workbench.createCheckpoint(""); workbenchDialog.openSection("changes") }
        else if (action === "spotlight") { ambient.openSpotlight() }
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
    Shortcut { sequence: "Alt+Space"; onActivated: ambient.toggleSpotlight() }
    Shortcut { sequence: "Alt+Shift+P"; enabled: hasProject && workspace.sessionId.length > 0; onActivated: workspace.pinSession(workspace.sessionId, !workspace.currentSessionPinned) }
    Connections {
        target: ambient
        function onRaiseMainWindowRequested() {
            window.page = 0
            window.show()
            window.raise()
            window.requestActivate()
            prompt.forceActiveFocus()
        }
        function onDictationTextReady(target, text) {
            if (target === "composer" && !ambient.autoSendVoice) {
                prompt.text = workspace.draftText
                prompt.cursorPosition = prompt.text.length
                prompt.forceActiveFocus()
            }
        }
    }
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

    property string chatMenuSessionId: ""
    property string chatMenuTitle: ""
    property bool chatMenuPinned: false
    property bool chatMenuUnread: false
    property bool chatMenuArchived: false
    property bool chatMenuCopyExpanded: false
    property string projectMenuId: ""
    property string projectMenuName: ""
    property string projectMenuRoot: ""
    property string deleteProjectId: ""
    property string deleteProjectName: ""
    property string deleteProjectRoot: ""
    property string exportChatSessionId: ""
    property string activeTopMenu: ""

    function promptDeleteProject(projData) {
        if (!projData || !projData.id) return
        deleteProjectId = String(projData.id)
        deleteProjectName = String(projData.name || "")
        deleteProjectRoot = String(projData.root || "")
        deleteProjectDialog.open()
    }

    function openRenameChat(sessionId, title) {
        if (!sessionId) return
        renameChatDialog.chatId = sessionId
        chatNameInput.text = title || ""
        renameChatDialog.open()
    }

    function openChatContextMenu(sessionData, targetItem) {
        if (!sessionData || !sessionData.id) return
        chatMenuSessionId = String(sessionData.id)
        chatMenuTitle = String(sessionData.title || sessionData.label || qsTr("Nuova chat"))
        chatMenuPinned = Boolean(sessionData.pinned)
        chatMenuUnread = Boolean(sessionData.unread)
        chatMenuArchived = Boolean(sessionData.archived)
        chatMenuCopyExpanded = false
        if (targetItem) {
            const pt = targetItem.mapToItem(window.contentItem, 0, targetItem.height + 4)
            chatContextMenu.x = Math.max(8, Math.min(window.width - chatContextMenu.width - 8, pt.x))
            chatContextMenu.y = Math.max(8, Math.min(window.height - chatContextMenu.implicitHeight - 16, pt.y))
        }
        chatContextMenu.open()
    }

    function openProjectContextMenu(projData, targetItem) {
        if (!projData || !projData.id) return
        projectMenuId = String(projData.id)
        projectMenuName = String(projData.name || "")
        projectMenuRoot = String(projData.root || "")
        if (targetItem) {
            const pt = targetItem.mapToItem(window.contentItem, 0, targetItem.height + 4)
            projectContextMenu.x = Math.max(8, Math.min(window.width - projectContextMenu.width - 8, pt.x))
            projectContextMenu.y = Math.max(8, Math.min(window.height - projectContextMenu.implicitHeight - 16, pt.y))
        }
        projectContextMenu.open()
    }

    function openTopMenu(menuKey, targetButton) {
        if (appTopMenuPopup.opened && activeTopMenu === menuKey) {
            appTopMenuPopup.close()
            activeTopMenu = ""
            return
        }
        activeTopMenu = menuKey
        if (targetButton) {
            const pt = targetButton.mapToItem(window.contentItem, 0, targetButton.height + 2)
            appTopMenuPopup.x = Math.max(6, Math.min(window.width - appTopMenuPopup.width - 8, pt.x))
            appTopMenuPopup.y = Math.max(24, pt.y)
        }
        if (!appTopMenuPopup.opened) appTopMenuPopup.open()
    }

    function topMenuItems(menuKey) {
        const hasChat = hasProject && workspace.sessionId.length > 0
        if (menuKey === "file") {
            return [
                { id: "menuFileNewProject", label: qsTr("Nuovo progetto…"), shortcut: "Ctrl+N", icon: "folder-plus", enabled: true, action: function() { newProject.open() } },
                { id: "menuFileNewChat", label: qsTr("Nuova chat"), shortcut: "Ctrl+Shift+N", icon: "plus", enabled: hasProject, action: function() { window.performAction("new_chat") } },
                { separator: true },
                { id: "menuFileOpenFolder", label: qsTr("Apri cartella del progetto"), shortcut: "", icon: "folder", enabled: hasProject, action: function() { workspace.openProjectFolder() } },
                { id: "menuFileAttach", label: qsTr("Allega file del progetto…"), shortcut: "@", icon: "attach", enabled: hasProject, action: function() { window.performAction("files") } },
                { separator: true },
                { id: "menuFileExportChat", label: qsTr("Esporta chat in Markdown…"), shortcut: "", icon: "export", enabled: hasChat, action: function() { window.exportChatSessionId = workspace.sessionId; chatMarkdownExport.open() } },
                { id: "menuFileExportMemory", label: qsTr("Esporta memoria del progetto…"), shortcut: "", icon: "memory", enabled: hasProject, action: function() { window.memoryExport = true; exportDialog.open() } },
                { id: "menuFileBackup", label: qsTr("Backup database locale"), shortcut: "", icon: "drive", enabled: workspace.connected, action: function() { workspace.backup() } },
                { id: "menuFileDeleteProject", label: qsTr("Elimina progetto…"), shortcut: "", icon: "trash", enabled: hasProject, action: function() { window.promptDeleteProject(workspace.project) } },
                { separator: true },
                { id: "menuFileSettings", label: qsTr("Impostazioni"), shortcut: "", icon: "settings", enabled: true, action: function() { window.page = 3 } },
                { id: "menuFileQuit", label: qsTr("Esci"), shortcut: "", icon: "close", enabled: true, action: function() { window.close() } }
            ]
        }
        if (menuKey === "edit") {
            return [
                { id: "menuEditSearch", label: qsTr("Cerca progetti e azioni…"), shortcut: "Ctrl+K", icon: "search", enabled: true, action: function() { actionDialog.open() } },
                { id: "menuEditPalette", label: qsTr("Tavolozza comandi…"), shortcut: "Ctrl+Shift+P", icon: "actions", enabled: true, action: function() { window.commandStart = -1; window.openActions("") } },
                { separator: true },
                { id: "menuEditCopyTranscript", label: qsTr("Copia trascrizione visibile"), shortcut: "Ctrl+Shift+C", icon: "copy", enabled: hasChat, action: function() { workspace.copyTranscript() } },
                { id: "menuEditCopyMarkdown", label: qsTr("Copia trascrizione Markdown"), shortcut: "", icon: "document", enabled: hasChat, action: function() { workspace.copySessionItem(workspace.sessionId, "markdown") } },
                { id: "menuEditCopyLast", label: qsTr("Copia ultima risposta"), shortcut: "", icon: "copy", enabled: hasChat, action: function() { workbench.copyLastOutput() } },
                { id: "menuEditCopyPath", label: qsTr("Copia percorso del progetto"), shortcut: "", icon: "link", enabled: hasProject, action: function() { workspace.copySessionItem(workspace.sessionId || "project", "path") } },
                { separator: true },
                { id: "menuEditPermissions", label: qsTr("Memoria e permessi del progetto…"), shortcut: "", icon: "lock", enabled: hasProject, action: function() { memoryDialog.open() } }
            ]
        }
        if (menuKey === "view") {
            return [
                { id: "menuViewProjects", label: qsTr("Conversazione e Progetti"), shortcut: "", icon: "projects", enabled: true, action: function() { window.page = 0 } },
                { id: "menuViewModels", label: qsTr("Modelli e Provider"), shortcut: "", icon: "models", enabled: true, action: function() { window.page = 1 } },
                { id: "menuViewActivity", label: qsTr("Attività registrate"), shortcut: "", icon: "activity", enabled: true, action: function() { window.page = 2 } },
                { id: "menuViewTeam", label: qsTr("Team del progetto"), shortcut: "", icon: "team", enabled: hasProject, action: function() { window.page = 4 } },
                { id: "menuViewSettings", label: qsTr("Impostazioni"), shortcut: "", icon: "settings", enabled: true, action: function() { window.page = 3 } },
                { separator: true },
                { id: "menuViewSidebar", label: qsTr("Alterna barra laterale"), shortcut: "Ctrl+B", icon: "sidebar", enabled: true, action: function() { workspace.cycleSidebarMode() } },
                { id: "menuViewDetails", label: window.detailsVisible ? qsTr("Nascondi dettagli del progetto") : qsTr("Mostra dettagli del progetto"), shortcut: "", icon: "details", enabled: hasProject, action: function() { workspace.setUiPreference("details_visible", !window.detailsVisible) } },
                { id: "menuViewToolLabels", label: workspace.toolLabels ? qsTr("Nascondi etichette strumenti") : qsTr("Mostra etichette strumenti"), shortcut: "", icon: "labels", enabled: true, action: function() { workspace.setToolLabels(!workspace.toolLabels) } },
                { separator: true },
                { id: "menuViewTheme", label: appearance.dark ? qsTr("Passa al tema chiaro") : qsTr("Passa al tema scuro"), shortcut: "", icon: appearance.dark ? "sun" : "moon", enabled: true, action: function() { appearance.setOption("mode", appearance.dark ? "light" : "dark") } }
            ]
        }
        if (menuKey === "chat") {
            return [
                { id: "menuChatNew", label: qsTr("Nuova chat"), shortcut: "Ctrl+Shift+N", icon: "plus", enabled: hasProject, action: function() { window.performAction("new_chat") } },
                { id: "menuChatRename", label: qsTr("Rinomina chat…"), shortcut: "", icon: "rename", enabled: hasChat, action: function() { window.openRenameChat(workspace.sessionId, workspace.sessionTitle) } },
                { id: "menuChatPin", label: workspace.currentSessionPinned ? qsTr("Rimuovi fissaggio") : qsTr("Fissa in alto"), shortcut: "", icon: "pin", enabled: hasChat, action: function() { workspace.pinSession(workspace.sessionId, !workspace.currentSessionPinned) } },
                { id: "menuChatUnread", label: workspace.currentSessionUnread ? qsTr("Segna come letta") : qsTr("Segna come non letta"), shortcut: "", icon: "unread", enabled: hasChat, action: function() { workspace.markSessionUnread(workspace.sessionId, !workspace.currentSessionUnread) } },
                { separator: true },
                { id: "menuChatBranch", label: qsTr("Dirama chat"), shortcut: "", icon: "branch", enabled: hasChat, action: function() { workspace.branchSession(workspace.sessionId) } },
                { id: "menuChatCompact", label: qsTr("Riduci contesto (Compatta)…"), shortcut: "", icon: "context", enabled: hasChat, action: function() { window.performAction("compact") } },
                { id: "menuChatExport", label: qsTr("Esporta Markdown…"), shortcut: "", icon: "export", enabled: hasChat, action: function() { window.exportChatSessionId = workspace.sessionId; chatMarkdownExport.open() } },
                { separator: true },
                { id: "menuChatArchive", label: workbench.sessionArchived ? qsTr("Ripristina dall'archivio") : qsTr("Archivia chat"), shortcut: "", icon: "archive", enabled: hasChat, action: function() { workspace.archiveSession(workspace.sessionId, !workbench.sessionArchived) } },
                { id: "menuChatDelete", label: qsTr("Elimina chat"), shortcut: "", icon: "trash", destructive: true, enabled: hasChat, action: function() { workspace.deleteSession(workspace.sessionId) } }
            ]
        }
        if (menuKey === "tools") {
            return [
                { id: "menuToolsEditor", label: qsTr("Editor di codice Kira"), shortcut: "", icon: "code", enabled: hasProject, action: function() { window.performAction("editor") } },
                { id: "menuToolsCanvas", label: qsTr("Canvas interattivo"), shortcut: "", icon: "studio", enabled: hasProject, action: function() { window.page = 0; workspace.web.hideBrowser(); workspace.studio.hideStudio(); workspace.canvas.toggleCanvas() } },
                { id: "menuToolsBrowser", label: qsTr("Browser integrato"), shortcut: "", icon: "browser", enabled: workspace.web.available, action: function() { window.page = 0; workspace.studio.hideStudio(); workspace.canvas.hideCanvas(); workspace.web.showBrowser() } },
                { id: "menuToolsStudio", label: qsTr("Studio creativo"), shortcut: "", icon: "image", enabled: workspace.studio.available, action: function() { window.page = 0; workspace.studio.showStudio() } },
                { separator: true },
                { id: "menuToolsQueue", label: qsTr("Coda del progetto"), shortcut: "", icon: "queue", enabled: hasProject, action: function() { workbenchDialog.openSection("queue") } },
                { id: "menuToolsChanges", label: qsTr("Modifiche e Checkpoint"), shortcut: "", icon: "file", enabled: hasProject, action: function() { workbenchDialog.openSection("changes") } },
                { id: "menuToolsGoal", label: qsTr("Obiettivo autonomo e Trigger"), shortcut: "", icon: "goal", enabled: hasProject, action: function() { workbenchDialog.openSection("goal") } },
                { id: "menuToolsArchives", label: qsTr("Archivio chat"), shortcut: "", icon: "archive", enabled: hasProject, action: function() { workbenchDialog.openSection("archives") } },
                { separator: true },
                { id: "menuToolsResearch", label: qsTr("Ricerca del progetto"), shortcut: "", icon: "research", enabled: hasProject, action: function() { researchDialog.open() } },
                { id: "menuToolsSoul", label: qsTr("Anima di Kira"), shortcut: "", icon: "anima", enabled: souls.available, action: function() { soulDialog.open() } },
                { id: "menuToolsSpotlight", label: qsTr("Spotlight rapido"), shortcut: "Alt+Space", icon: "spotlight", enabled: true, action: function() { ambient.openSpotlight() } }
            ]
        }
        return [
            { id: "menuHelpGuide", label: qsTr("Guida iniziale"), shortcut: "", icon: "help", enabled: true, action: function() { onboardingDialog.open() } },
            { id: "menuHelpResume", label: qsTr("Riprendi lavoro"), shortcut: "Ctrl+Shift+R", icon: "resume", enabled: hasProject, action: function() { resumeDialog.open() } },
            { id: "menuHelpUsage", label: qsTr("Uso e risorse misurati"), shortcut: "", icon: "usage", enabled: true, action: function() { metricsDialog.open() } },
            { id: "menuHelpDiagnostics", label: qsTr("Diagnostica di sistema"), shortcut: "", icon: "details", enabled: true, action: function() { diagnosticsDialog.open() } },
            { id: "menuHelpUpdates", label: qsTr("Controlla aggiornamenti"), shortcut: "", icon: "update", enabled: true, action: function() { updateDialog.open() } }
        ]
    }

    KiraPanelHost {
        id: panelHost
        x: mainRow.x + workspaceColumn.x
        y: mainRow.y + workspaceColumn.y + workspaceStack.y
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

    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        Rectangle {
            id: appMenuBar
            objectName: "appMenuBar"
            Layout.fillWidth: true
            implicitHeight: window.compact ? 24 : 28
            color: appearance.colors.sidebar
            Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: appearance.colors.border }
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 10
                anchors.rightMargin: 12
                spacing: 2
                Repeater {
                    model: [
                        { key: "file", label: qsTr("File"), objectName: "appMenuFile" },
                        { key: "edit", label: qsTr("Modifica"), objectName: "appMenuEdit" },
                        { key: "view", label: qsTr("Visualizza"), objectName: "appMenuView" },
                        { key: "chat", label: qsTr("Chat"), objectName: "appMenuChat" },
                        { key: "tools", label: qsTr("Strumenti"), objectName: "appMenuTools" },
                        { key: "help", label: qsTr("Aiuto"), objectName: "appMenuHelp" }
                    ]
                    delegate: KButton {
                        id: topMenuBtn
                        required property var modelData
                        objectName: modelData.objectName
                        text: modelData.label
                        subtle: true
                        quietSelection: true
                        checked: appTopMenuPopup.opened && window.activeTopMenu === modelData.key
                        implicitHeight: appMenuBar.implicitHeight - 4
                        implicitWidth: Math.max(42, topMenuBtnMetrics.advanceWidth + 18)
                        font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 1)
                        Accessible.name: modelData.label
                        TextMetrics { id: topMenuBtnMetrics; text: topMenuBtn.text; font: topMenuBtn.font }
                        onClicked: window.openTopMenu(modelData.key, topMenuBtn)
                        onHoveredChanged: {
                            if (hovered && appTopMenuPopup.opened && window.activeTopMenu !== modelData.key) {
                                window.openTopMenu(modelData.key, topMenuBtn)
                            }
                        }
                    }
                }
                Item { Layout.fillWidth: true }
                Text {
                    visible: window.width >= 960
                    text: workspace.project.name ? (workspace.project.name + (workspace.sessionId ? "  ·  " + workspace.sessionTitle : "")) : "Kira Desktop"
                    color: muted
                    font.family: appearance.uiFont.family
                    font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 2)
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    Layout.maximumWidth: Math.max(140, window.width * 0.35)
                }
            }
        }
        RowLayout {
            id: mainRow
            Layout.fillWidth: true
            Layout.fillHeight: true
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
                    spacing: 2
                    Text {
                        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText
                        text: workspace.sessionFilter === "pinned" ? qsTr("PROGETTI · FISSATE")
                            : workspace.sessionFilter === "unread" ? qsTr("PROGETTI · NON LETTE")
                            : workspace.sessionFilter === "archived" ? qsTr("PROGETTI · ARCHIVIATE")
                            : qsTr("PROGETTI E CHAT")
                        color: workspace.sessionFilter !== "all" ? appearance.colors.accent : muted
                        font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); font.letterSpacing: 1.1
                        Layout.fillWidth: true; Layout.minimumWidth: 0; wrapMode: Text.WordWrap
                    }
                    KButton {
                        id: chatFilterButton
                        objectName: "chatFilterButton"
                        iconName: "filter"
                        subtle: true
                        quietSelection: true
                        checked: workspace.sessionFilter !== "all" || chatFilterMenu.opened
                        implicitWidth: Math.ceil(26 * appearance.uiFont.pixelSize / 13)
                        implicitHeight: 26 + Math.max(0, appearance.uiFont.pixelSize - 13)
                        onClicked: {
                            const pt = chatFilterButton.mapToItem(window.contentItem, 0, chatFilterButton.height + 4)
                            chatFilterMenu.x = Math.max(8, Math.min(window.width - chatFilterMenu.width - 8, pt.x))
                            chatFilterMenu.y = Math.max(8, Math.min(window.height - chatFilterMenu.implicitHeight - 16, pt.y))
                            if (chatFilterMenu.opened) chatFilterMenu.close()
                            else chatFilterMenu.open()
                        }
                        Accessible.name: qsTr("Filtra chat")
                        toolTipText: qsTr("Filtra chat (Tutte, Fissate, Non lette, Archiviate)")
                    }
                    KButton {
                        objectName: "sidebarNewProjectButton"
                        text: "+"; subtle: true
                        implicitWidth: Math.ceil(26 * appearance.uiFont.pixelSize / 13)
                        implicitHeight: 26 + Math.max(0, appearance.uiFont.pixelSize - 13)
                        onClicked: newProject.open()
                        Accessible.name: qsTr("Nuovo progetto")
                        toolTipText: qsTr("Nuovo progetto · Ctrl+N")
                    }
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
                    ScrollBar.vertical: ScrollBar {
                        interactive: projectList.contentHeight > projectList.height
                        policy: projectList.contentHeight > projectList.height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
                    }
                    delegate: Column {
                        id: projectEntry
                        required property var modelData
                        property bool expanded: true
                        readonly property bool projectMenuOpen: projectContextMenu.opened && window.projectMenuId === modelData.id
                        readonly property bool projectActionsVisible: projectEntryHover.hovered || projectRowHover.hovered || projectMenuOpen
                        width: projectList.width - (projectList.contentHeight > projectList.height ? 10 : 0)
                        spacing: 3
                        HoverHandler { id: projectEntryHover }
                        Item {
                            id: projectHeaderRow
                            width: parent.width
                            height: 36 + Math.max(0, appearance.uiFont.pixelSize - 13)
                            HoverHandler { id: projectRowHover }
                            KButton {
                                id: projectSelectButton
                                objectName: "projectSelect_" + projectEntry.modelData.id
                                anchors.fill: parent
                                cornerRadius: 8
                                forceHover: projectRowHover.hovered || projectEntry.projectMenuOpen
                                text: (workspace.project.id === projectEntry.modelData.id && projectEntry.expanded ? "▾  " : "▸  ") + projectEntry.modelData.name
                                leftAligned: true; subtle: true
                                font.weight: workspace.project.id === projectEntry.modelData.id ? Font.DemiBold : Font.Normal
                                toolTipText: projectEntry.modelData.root
                                onClicked: { if (workspace.project.id === projectEntry.modelData.id) projectEntry.expanded = !projectEntry.expanded; else { projectEntry.expanded = true; workspace.openProject(projectEntry.modelData.id) } window.page = 0; window.followOutput = true }
                            }
                            TapHandler {
                                acceptedButtons: Qt.RightButton
                                onTapped: window.openProjectContextMenu(projectEntry.modelData, projectMenuButton)
                            }
                            Row {
                                id: projectActionsRow
                                anchors.right: parent.right; anchors.rightMargin: 3
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 1
                                opacity: projectEntry.projectActionsVisible ? 1.0 : 0.0
                                Behavior on opacity {
                                    enabled: window.motionEnabled
                                    NumberAnimation { duration: 120; easing.type: Easing.OutCubic }
                                }
                                KButton {
                                    id: projectMenuButton
                                    objectName: "projectMenu_" + projectEntry.modelData.id
                                    iconName: "more-vertical"; iconSize: 14; cornerRadius: 6; subtle: true
                                    checked: projectEntry.projectMenuOpen; quietSelection: true
                                    implicitWidth: Math.ceil(24 * appearance.uiFont.pixelSize / 13)
                                    implicitHeight: Math.ceil(24 * appearance.uiFont.pixelSize / 13)
                                    toolTipText: qsTr("Opzioni progetto")
                                    Accessible.name: qsTr("Opzioni progetto %1").arg(projectEntry.modelData.name)
                                    onClicked: window.openProjectContextMenu(projectEntry.modelData, projectMenuButton)
                                }
                                KButton {
                                    objectName: "projectNewChat_" + projectEntry.modelData.id
                                    iconName: "plus"; iconSize: 14; cornerRadius: 6; subtle: true
                                    implicitWidth: Math.ceil(24 * appearance.uiFont.pixelSize / 13)
                                    implicitHeight: Math.ceil(24 * appearance.uiFont.pixelSize / 13)
                                    toolTipText: qsTr("Nuova chat nel progetto")
                                    Accessible.name: qsTr("Nuova chat in %1").arg(projectEntry.modelData.name)
                                    onClicked: { projectEntry.expanded = true; workspace.newChatInProject(projectEntry.modelData.id); window.page = 0 }
                                }
                            }
                        }
                        Column {
                            id: chatChildren
                            x: 10; width: parent.width - 10; spacing: 2
                            visible: workspace.project.id === projectEntry.modelData.id && projectEntry.expanded
                            Repeater {
                                model: chatChildren.visible ? workspace.sessions : []
                                delegate: Item {
                                    id: chatEntry
                                    required property var modelData
                                    readonly property var activity: window.chatIndicators[modelData.id] || ({})
                                    readonly property bool indicatorVisible: !!activity.state
                                    readonly property bool isPinned: Boolean(modelData.pinned)
                                    readonly property bool isUnread: Boolean(modelData.unread)
                                    readonly property bool isArchived: Boolean(modelData.archived)
                                    readonly property bool menuOpenForChat: chatContextMenu.opened && window.chatMenuSessionId === modelData.id
                                    readonly property bool actionsVisible: chatRowHover.hovered || menuOpenForChat
                                    readonly property real listY: projectEntry.y + chatChildren.y + y
                                    width: chatChildren.width; height: 34 + Math.max(0, appearance.uiFont.pixelSize - 13)
                                    HoverHandler { id: chatRowHover }
                                    KButton {
                                        id: chatSelect
                                        objectName: "chatSelect_" + chatEntry.modelData.id
                                        anchors.fill: parent
                                        cornerRadius: 8
                                        forceHover: chatRowHover.hovered || chatEntry.menuOpenForChat
                                        text: chatEntry.modelData.title || chatEntry.modelData.label || qsTr("Nuova chat")
                                        checked: workspace.sessionId === chatEntry.modelData.id
                                        quietSelection: true; selectionMark: true
                                        subtle: true; leftAligned: true
                                        font.weight: chatEntry.isUnread ? Font.DemiBold : Font.Normal
                                        toolTipText: text + " · " + (chatEntry.modelData.backend === "deepseek_web" ? "DeepSeek Web" : kformat.providerLabel(chatEntry.modelData.backend) + " · " + chatEntry.modelData.model)
                                            + (chatEntry.indicatorVisible ? " · " + chatEntry.activity.label : "")
                                        Accessible.name: qsTr("Apri chat %1").arg(text)
                                            + (chatEntry.indicatorVisible ? " · " + chatEntry.activity.label : "")
                                        TapHandler {
                                            acceptedButtons: Qt.RightButton
                                            onTapped: window.openChatContextMenu(chatEntry.modelData, chatSelect)
                                        }
                                        contentItem: Item {
                                            Rectangle {
                                                id: chatUnreadDot
                                                objectName: "chatUnreadDot_" + chatEntry.modelData.id
                                                visible: chatEntry.isUnread
                                                width: 7; height: 7; radius: 3.5
                                                color: appearance.colors.accent
                                                anchors.left: parent.left; anchors.leftMargin: 4
                                                anchors.verticalCenter: parent.verticalCenter
                                            }
                                            KIcon {
                                                id: chatPinBadge
                                                objectName: "chatPinBadge_" + chatEntry.modelData.id
                                                name: "pin"
                                                visible: chatEntry.isPinned && !chatEntry.isUnread
                                                width: 11; height: 11
                                                ink: appearance.colors.accent
                                                anchors.left: parent.left; anchors.leftMargin: 3
                                                anchors.verticalCenter: parent.verticalCenter
                                            }
                                            Text {
                                                anchors.fill: parent
                                                leftPadding: (chatEntry.isUnread || chatEntry.isPinned) ? 16 : 10
                                                rightPadding: (chatEntry.actionsVisible ? chatActionsRow.width + 8 : (chatAgeLabel.visible ? chatAgeLabel.implicitWidth + 12 : 8)) + (chatEntry.indicatorVisible && !chatEntry.actionsVisible ? 16 : 0)
                                                text: chatSelect.text; font: chatSelect.font; color: chatSelect.foreground
                                                textFormat: Text.PlainText; elide: Text.ElideRight
                                                verticalAlignment: Text.AlignVCenter
                                            }
                                            Text {
                                                id: chatAgeLabel
                                                objectName: "chatAge_" + chatEntry.modelData.id
                                                anchors.right: chatEntry.indicatorVisible ? chatStatusIcon.left : parent.right
                                                anchors.rightMargin: chatEntry.indicatorVisible ? 5 : 8
                                                anchors.verticalCenter: parent.verticalCenter
                                                text: { localization.revision; return kformat.relativeTime(chatEntry.modelData.updated_at || chatEntry.modelData.created_at) }
                                                visible: text.length > 0 && !chatEntry.actionsVisible
                                                color: appearance.colors.muted
                                                font.family: appearance.uiFont.family
                                                font.styleName: appearance.uiFont.styleName
                                                font.weight: appearance.uiFont.weight
                                                font.italic: appearance.uiFont.italic
                                                font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 2)
                                                textFormat: Text.PlainText
                                            }
                                            StatusIndicator {
                                                id: chatStatusIcon
                                                objectName: "chatStatus_" + chatEntry.modelData.id
                                                anchors.right: parent.right; anchors.rightMargin: 5
                                                anchors.verticalCenter: parent.verticalCenter
                                                visible: chatEntry.indicatorVisible && !chatEntry.actionsVisible
                                                state: chatEntry.activity.state || "idle"
                                                diameter: 10
                                                animate: window.motionEnabled && chatEntry.visible
                                                    && chatEntry.listY + chatEntry.height > projectList.contentY
                                                    && chatEntry.listY < projectList.contentY + projectList.height
                                            }
                                        }
                                        onClicked: { const sid = chatEntry.modelData.id; window.page = 0; workspace.selectSession(sid) }
                                    }
                                    KButton {
                                        objectName: "chatRename_" + chatEntry.modelData.id
                                        anchors.right: chatActionsRow.left
                                        anchors.verticalCenter: parent.verticalCenter
                                        implicitWidth: 4
                                        implicitHeight: 20
                                        opacity: 0.0
                                        subtle: true
                                        toolTipText: qsTr("Rinomina chat"); Accessible.name: qsTr("Rinomina chat")
                                        onClicked: window.openRenameChat(chatEntry.modelData.id, chatEntry.modelData.title || "")
                                    }
                                    Row {
                                        id: chatActionsRow
                                        anchors.right: parent.right; anchors.rightMargin: 3
                                        anchors.verticalCenter: parent.verticalCenter
                                        spacing: 1
                                        opacity: chatEntry.actionsVisible ? 1.0 : 0.0
                                        Behavior on opacity {
                                            enabled: window.motionEnabled
                                            NumberAnimation { duration: 120; easing.type: Easing.OutCubic }
                                        }
                                        KButton {
                                            id: chatMenuButton
                                            objectName: "chatMenu_" + chatEntry.modelData.id
                                            iconName: "more-vertical"; iconSize: 14; cornerRadius: 6; subtle: true
                                            checked: chatEntry.menuOpenForChat; quietSelection: true
                                            implicitWidth: Math.ceil(22 * appearance.uiFont.pixelSize / 13)
                                            implicitHeight: Math.ceil(24 * appearance.uiFont.pixelSize / 13)
                                            toolTipText: qsTr("Opzioni chat")
                                            Accessible.name: qsTr("Opzioni chat")
                                            onClicked: window.openChatContextMenu(chatEntry.modelData, chatMenuButton)
                                        }
                                        KButton {
                                            id: chatPinButton
                                            objectName: "chatPin_" + chatEntry.modelData.id
                                            iconName: "pin"; iconSize: 14; cornerRadius: 6; subtle: true
                                            checked: chatEntry.isPinned; quietSelection: true
                                            implicitWidth: Math.ceil(22 * appearance.uiFont.pixelSize / 13)
                                            implicitHeight: Math.ceil(24 * appearance.uiFont.pixelSize / 13)
                                            toolTipText: chatEntry.isPinned ? qsTr("Rimuovi fissaggio") : qsTr("Fissa chat")
                                            Accessible.name: chatEntry.isPinned ? qsTr("Rimuovi fissaggio") : qsTr("Fissa chat")
                                            onClicked: workspace.pinSession(chatEntry.modelData.id, !chatEntry.isPinned)
                                        }
                                        KButton {
                                            id: chatArchiveButton
                                            objectName: "chatArchive_" + chatEntry.modelData.id
                                            iconName: "archive"; iconSize: 14; cornerRadius: 6; subtle: true
                                            checked: chatEntry.isArchived; quietSelection: true
                                            implicitWidth: Math.ceil(22 * appearance.uiFont.pixelSize / 13)
                                            implicitHeight: Math.ceil(24 * appearance.uiFont.pixelSize / 13)
                                            toolTipText: chatEntry.isArchived ? qsTr("Ripristina dall'archivio") : qsTr("Archivia chat")
                                            Accessible.name: chatEntry.isArchived ? qsTr("Ripristina dall'archivio") : qsTr("Archivia chat")
                                            onClicked: workspace.archiveSession(chatEntry.modelData.id, !chatEntry.isArchived)
                                        }
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
                                    cacheBuffer: count <= 48 ? 10000000 : 320
                                    reuseItems: false
                                    // Keep layout active while replay is staged, but reveal only
                                    // after the final rows have been positioned at the latest reply.
                                    opacity: historyReady && !workspace.chatLoading ? 1 : 0
                                    enabled: historyReady && !workspace.chatLoading
                                    property bool historyReady: !workspace.chatLoading
                                    property bool followPending: false
                                    property bool layoutStepping: false
                                    function scrollToEnd() {
                                        layoutStepping = true
                                        forceLayout()
                                        if (count > 0 && count <= 48 && (!itemAtIndex(0) || !itemAtIndex(count - 1))) {
                                            if (!itemAtIndex(0)) {
                                                positionViewAtBeginning()
                                                forceLayout()
                                            }
                                            var probe = 0
                                            var steps = 0
                                            while (!itemAtIndex(count - 1) && steps < count) {
                                                while (probe + 1 < count && itemAtIndex(probe + 1)) probe++
                                                var last = itemAtIndex(probe)
                                                if (!last) break
                                                contentY = Math.max(contentY + Math.max(64, height * 0.8), last.y + Math.max(1, last.height - 1))
                                                forceLayout()
                                                steps++
                                            }
                                        }
                                        positionViewAtEnd()
                                        forceLayout()
                                        positionViewAtEnd()
                                        layoutStepping = false
                                    }
                                    function scheduleFollow() {
                                        if (workspace.chatLoading || followPending) return
                                        if (!window.followOutput && historyReady) return
                                        followPending = true
                                        Qt.callLater(finishFollow)
                                    }
                                    function finishFollow() {
                                        if (!workspace.chatLoading) {
                                            if (window.followOutput || !historyReady) {
                                                scrollToEnd()
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
                                        if (layoutStepping || appearanceRelayout) return
                                        if (count > 0 && height > 0) {
                                            var minimum = originY
                                            var maximum = Math.max(minimum, minimum + contentHeight - height)
                                            if (contentY < minimum || contentY > maximum) {
                                                contentY = Math.max(minimum, Math.min(maximum, contentY))
                                                return
                                            }
                                        }
                                        if (window.followOutput) return
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
                                        function applyAnchor() {
                                            conversation.forceLayout()
                                            var visibleItem = conversation.itemAtIndex(row)
                                            if (visibleItem) {
                                                var minimum = conversation.originY
                                                var maximum = Math.max(minimum, minimum + conversation.contentHeight - conversation.height)
                                                conversation.contentY = Math.max(minimum, Math.min(maximum, visibleItem.y - offset))
                                            }
                                        }
                                        Qt.callLater(function() {
                                            applyAnchor()
                                            Qt.callLater(function() {
                                                applyAnchor()
                                                conversation.appearanceRelayout = false
                                                conversation.rememberReadingAnchor()
                                            })
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
                                    Connections {
                                        target: transcriptModel
                                        function onModelReset() {
                                            if (!workspace.chatLoading && conversation.count > 48 && window.followOutput) {
                                                conversation.positionViewAtEnd()
                                            }
                                            conversation.scheduleFollow()
                                        }
                                    }
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
                                            enabled: entry.motionAvailable || entry.showSource
                                            function onContentYChanged() {
                                                if (entry.showSource && !conversation.layoutStepping
                                                    && (entry.y + entry.height < conversation.contentY - 450
                                                        || entry.y > conversation.contentY + conversation.height + 450)) {
                                                    entry.showSource = false
                                                }
                                                entry.scheduleViewportUpdate()
                                            }
                                            function onHeightChanged() { entry.scheduleViewportUpdate() }
                                        }
                                        readonly property bool stateNeedsAttention: ["failed", "interrupted", "cancelled", "errore", "incompleto"].indexOf(status) >= 0
                                        readonly property real previewHeight: Math.max(1, Math.floor(Math.min(144, Math.max(messageFontMetrics.lineSpacing, conversation.height - 100)) / Math.ceil(messageFontMetrics.lineSpacing))) * Math.ceil(messageFontMetrics.lineSpacing)
                                        FontMetrics { id: messageFontMetrics; font: messageText.font }
                                        ListView.onPooled: pooled = true
                                        ListView.onReused: { pooled = false; showSource = false }
                                        width: Math.min(conversation.width - conversationBar.width - 8, appearance.fullWidth ? Infinity : 900)
                                        readonly property real targetX: Math.max(0, (conversation.width - conversationBar.width - 8 - width) / 2)
                                        x: targetX
                                        onTargetXChanged: x = targetX
                                        onXChanged: if (!pooled && x !== targetX) x = targetX
                                        visible: !toolGroupHidden && !pooled
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
                                                    Text { font.family: appearance.codeFont.family; font.styleName: appearance.codeFont.styleName; font.weight: appearance.codeFont.weight; font.italic: appearance.codeFont.italic; objectName: "messageDuration_" + entry.index; textFormat: Text.PlainText; text: { workspace.revision; if (entry.kind !== "assistant" || !entry.runId) return ""; if (entry.runId === workspace.selectedActiveRunId && activityIndicator.clockRunning) return "⏱ " + activityIndicator.elapsedText; var d = workspace.runDurationText(entry.runId) || ""; return d.length > 0 ? "⏱ " + d : "" } color: muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 3); visible: entry.kind === "assistant" && text.length > 0 }
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
                                                    text: (entry.kind === "tool" && entry.toolGroupCount > 0 && !entry.toolGroupExpanded) ? "" : (entry.kind === "assistant" && !entry.showSource ? markdownRenderer.renderForAppearance(entry.body, appearance.revision + localization.revision) : entry.kind === "tool" && !entry.outputExpanded ? entry.previewBody : entry.body)
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
                                KButton { anchors.right: parent.right; anchors.rightMargin: 35; anchors.bottom: parent.bottom; anchors.bottomMargin: 16; text: qsTr("↓  Ultimi aggiornamenti"); visible: !window.followOutput && conversation.historyReady && !workspace.chatLoading; onClicked: { window.followOutput = true; conversation.scrollToEnd() } }
                            }
                            RunErrorPanel {
                                failure: workspace.runFailure
                                Layout.fillWidth: true; Layout.leftMargin: 24; Layout.rightMargin: 24; Layout.bottomMargin: 8
                                Layout.maximumWidth: appearance.fullWidth ? Infinity : 900
                                Layout.alignment: Qt.AlignHCenter
                                onReportRequested: runErrorReportDialog.open()
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
                                    id: activityIndicator
                                    objectName: "activityIndicator"
                                    Layout.fillWidth: true; Layout.minimumWidth: 0
                                    Layout.maximumWidth: implicitWidth
                                    label: workbench.activityLabel
                                    outcome: workbench.activityOutcome
                                    motionEnabled: window.chatMotionEnabled
                                    startedAt: workspace.activityStartedAt
                                    durationText: workspace.activityDurationText
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
                                    Rectangle {
                                        objectName: "composerAmbientChip"
                                        visible: ambient.hasCapturedContext
                                        Layout.fillWidth: true
                                        color: appearance.colors.surface
                                        radius: 8
                                        border.color: appearance.colors.border
                                        border.width: 1
                                        implicitHeight: composerChipRow.implicitHeight + 10
                                        RowLayout {
                                            id: composerChipRow
                                            anchors.fill: parent
                                            anchors.margins: 5
                                            spacing: 6
                                            KIcon { name: "context"; ink: appearance.colors.accent; width: 14; height: 14 }
                                            Text {
                                                objectName: "composerAmbientChipLabel"
                                                text: ambient.contextChipLabel
                                                font.family: appearance.uiFont.family
                                                font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 2)
                                                font.bold: true
                                                color: appearance.colors.text
                                                textFormat: Text.PlainText
                                                elide: Text.ElideRight
                                            }
                                            Text {
                                                objectName: "composerAmbientChipPreview"
                                                visible: ambient.contextPreview.length > 0
                                                text: "· " + ambient.contextPreview
                                                font.family: appearance.codeFont.family
                                                font.pixelSize: Math.max(10, appearance.codeFont.pixelSize - 2)
                                                color: appearance.colors.muted
                                                textFormat: Text.PlainText
                                                elide: Text.ElideRight
                                                maximumLineCount: 1
                                                Layout.fillWidth: true
                                            }
                                            Item { visible: ambient.contextPreview.length === 0; Layout.fillWidth: true }
                                            KButton {
                                                objectName: "composerClearAmbientButton"
                                                subtle: true; iconName: "close"
                                                implicitWidth: 22; implicitHeight: 22
                                                Accessible.name: qsTr("Rimuovi contesto finestra attiva")
                                                toolTipText: qsTr("Rimuovi contesto finestra attiva")
                                                onClicked: ambient.clearContext()
                                            }
                                        }
                                    }
                                    RowLayout {
                                        id: composerMentionsBar
                                        objectName: "composerMentionsBar"
                                        visible: mentions.hasActiveChips
                                        Layout.fillWidth: true
                                        spacing: 6
                                        Flow {
                                            objectName: "composerMentionsFlow"
                                            Layout.fillWidth: true
                                            spacing: 6
                                            Repeater {
                                                model: mentions.activeChips
                                                delegate: Rectangle {
                                                    required property var modelData
                                                    required property int index
                                                    objectName: "composerMentionChip_" + index
                                                    radius: 8
                                                    color: appearance.colors.surface
                                                    border.color: appearance.colors.accent
                                                    border.width: 1
                                                    implicitWidth: chipInnerRow.implicitWidth + 12
                                                    implicitHeight: 24
                                                    RowLayout {
                                                        id: chipInnerRow
                                                        anchors.centerIn: parent
                                                        spacing: 5
                                                        Rectangle {
                                                            radius: 4
                                                            color: Qt.rgba(appearance.colors.accent.r, appearance.colors.accent.g, appearance.colors.accent.b, 0.16)
                                                            implicitWidth: chipBadgeText.implicitWidth + 8
                                                            implicitHeight: 16
                                                            Text {
                                                                id: chipBadgeText
                                                                anchors.centerIn: parent
                                                                text: modelData.badge || "CTX"
                                                                font.family: appearance.codeFont.family
                                                                font.pixelSize: 9
                                                                font.bold: true
                                                                color: appearance.colors.accent
                                                                textFormat: Text.PlainText
                                                            }
                                                        }
                                                        Text {
                                                            text: modelData.label || ""
                                                            font.family: appearance.codeFont.family
                                                            font.pixelSize: Math.max(10, appearance.codeFont.pixelSize - 2)
                                                            color: appearance.colors.text
                                                            textFormat: Text.PlainText
                                                        }
                                                        KButton {
                                                            objectName: "removeMentionChip_" + index
                                                            subtle: true
                                                            iconName: "close"
                                                            implicitWidth: 18
                                                            implicitHeight: 18
                                                            Accessible.name: qsTr("Rimuovi menzione")
                                                            toolTipText: qsTr("Rimuovi %1").arg(modelData.label || "")
                                                            onClicked: mentions.removeChip(index)
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                        KButton {
                                            objectName: "clearAllMentionsButton"
                                            visible: mentions.activeChipCount > 1
                                            subtle: true
                                            text: qsTr("Svuota")
                                            implicitHeight: 24
                                            onClicked: mentions.clearChips()
                                        }
                                    }
                                    Rectangle {
                                        id: composerInlinePopup
                                        objectName: "composerInlinePopup"
                                        visible: mentions.inlinePopupVisible
                                        Layout.fillWidth: true
                                        radius: 10
                                        color: appearance.colors.surface
                                        border.color: appearance.colors.accent
                                        border.width: 1
                                        implicitHeight: inlinePopupCol.implicitHeight + 12
                                        ColumnLayout {
                                            id: inlinePopupCol
                                            anchors.fill: parent
                                            anchors.margins: 6
                                            spacing: 3
                                            RowLayout {
                                                Layout.fillWidth: true
                                                spacing: 6
                                                Text {
                                                    text: mentions.inlineTriggerKind === "@" ? qsTr("Suggerimenti @Mentions (Invio/Tab per completare)") : qsTr("Comandi rapidi /Slash (Invio/Tab per completare)")
                                                    font.family: appearance.uiFont.family
                                                    font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 3)
                                                    font.bold: true
                                                    color: appearance.colors.muted
                                                    textFormat: Text.PlainText
                                                    Layout.fillWidth: true
                                                }
                                                KButton {
                                                    subtle: true; iconName: "close"
                                                    implicitWidth: 18; implicitHeight: 18
                                                    Accessible.name: qsTr("Chiudi suggerimenti")
                                                    onClicked: mentions.dismissInlinePopup()
                                                }
                                            }
                                            Repeater {
                                                model: mentions.inlineSuggestions
                                                delegate: Rectangle {
                                                    required property var modelData
                                                    required property int index
                                                    objectName: "inlineSuggestionRow_" + index
                                                    readonly property bool current: index === mentions.inlineSelectedIndex
                                                    Layout.fillWidth: true
                                                    implicitHeight: 28
                                                    radius: 6
                                                    color: current ? Qt.rgba(appearance.colors.accent.r, appearance.colors.accent.g, appearance.colors.accent.b, 0.14) : "transparent"
                                                    RowLayout {
                                                        anchors.fill: parent
                                                        anchors.leftMargin: 8; anchors.rightMargin: 8
                                                        spacing: 8
                                                        Rectangle {
                                                            radius: 4
                                                            color: Qt.rgba(appearance.colors.accent.r, appearance.colors.accent.g, appearance.colors.accent.b, 0.18)
                                                            implicitWidth: sugBadge.implicitWidth + 8
                                                            implicitHeight: 18
                                                            Text {
                                                                id: sugBadge
                                                                anchors.centerIn: parent
                                                                text: modelData.badge || "CTX"
                                                                font.family: appearance.codeFont.family
                                                                font.pixelSize: 9
                                                                font.bold: true
                                                                color: appearance.colors.accent
                                                                textFormat: Text.PlainText
                                                            }
                                                        }
                                                        Text {
                                                            text: modelData.label || ""
                                                            font.family: appearance.uiFont.family
                                                            font.pixelSize: Math.max(11, appearance.uiFont.pixelSize - 1)
                                                            font.bold: current
                                                            color: appearance.colors.text
                                                            textFormat: Text.PlainText
                                                        }
                                                        Text {
                                                            text: modelData.description || ""
                                                            font.family: appearance.uiFont.family
                                                            font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 2)
                                                            color: appearance.colors.muted
                                                            textFormat: Text.PlainText
                                                            elide: Text.ElideRight
                                                            Layout.fillWidth: true
                                                        }
                                                    }
                                                    MouseArea {
                                                        anchors.fill: parent
                                                        cursorShape: appearance.pointerCursors ? Qt.PointingHandCursor : Qt.ArrowCursor
                                                        onClicked: {
                                                            var nextText = mentions.applyInlineSuggestion(index, prompt.text, prompt.cursorPosition)
                                                            prompt.text = nextText
                                                            prompt.cursorPosition = nextText.length
                                                            prompt.forceActiveFocus()
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                    Text {
                                        objectName: "composerVoiceStatus"
                                        visible: ambient.voiceStatusLabel.length > 0
                                        text: ambient.voiceStatusLabel
                                        Layout.fillWidth: true
                                        font.family: appearance.uiFont.family
                                        font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 2)
                                        color: ambient.recording ? appearance.colors.danger : appearance.colors.accent
                                        textFormat: Text.PlainText
                                        elide: Text.ElideRight
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
                                            placeholderText: workbench.guidanceMode ? qsTr("Aggiungi indicazioni al turno in corso…") : qsTr("Descrivi il prossimo passo… (@ per contesto, / per comandi)")
                                            onTextChanged: {
                                                workspace.setDraftText(text)
                                                if (!activeFocus || window.restoringDraft) return
                                                mentions.updateInputCursor(text, cursorPosition)
                                                var before = text.slice(0, cursorPosition)
                                                if (/(^|\s)\/$/.test(before) && !actionPalette.visible) {
                                                    window.commandStart = before.length - 1
                                                    window.openActions("")
                                                } else if (/(^|\s)@$/.test(before) && !projectFileDialog.visible) {
                                                    window.mentionStart = before.length - 1
                                                    window.openProjectFiles()
                                                }
                                            }
                                            onCursorPositionChanged: {
                                                if (activeFocus && !window.restoringDraft) {
                                                    mentions.updateInputCursor(text, cursorPosition)
                                                }
                                            }
                                            wrapMode: TextEdit.Wrap; selectByMouse: true; color: appearance.colors.text
                                            textFormat: TextEdit.PlainText; background: null
                                            font.pixelSize: window.chatFontSize; leftPadding: 2; rightPadding: 2; topPadding: 4; bottomPadding: 4
                                            Keys.onPressed: function(event) {
                                                if (mentions.inlinePopupVisible) {
                                                    if (event.key === Qt.Key_Down) {
                                                        mentions.moveInlineSelection(1)
                                                        event.accepted = true
                                                        return
                                                    }
                                                    if (event.key === Qt.Key_Up) {
                                                        mentions.moveInlineSelection(-1)
                                                        event.accepted = true
                                                        return
                                                    }
                                                    if (event.key === Qt.Key_Escape) {
                                                        mentions.dismissInlinePopup()
                                                        event.accepted = true
                                                        return
                                                    }
                                                    if (event.key === Qt.Key_Tab || ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && !(event.modifiers & (Qt.ControlModifier | Qt.ShiftModifier)))) {
                                                        var nextText = mentions.applyInlineSuggestion(-1, prompt.text, prompt.cursorPosition)
                                                        prompt.text = nextText
                                                        prompt.cursorPosition = nextText.length
                                                        event.accepted = true
                                                        return
                                                    }
                                                }
                                                if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && (event.modifiers & Qt.ControlModifier)) {
                                                    window.submitComposer()
                                                    event.accepted = true
                                                }
                                            }
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
                                        readonly property bool narrow: chatColumn.width < 860 || appearance.uiFont.pixelSize > 15
                                        // Labels give way before the rows stack, so the model chip keeps its text.
                                        readonly property bool tight: chatColumn.width < 960 || appearance.uiFont.pixelSize > 15
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
                                        KButton { objectName: "projectFilesButton"; implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13); cornerRadius: 15; text: composerChips.tight ? "" : qsTr("File"); iconName: "file"; subtle: true; Accessible.name: qsTr("File del progetto"); onClicked: { window.mentionStart = -1; window.openProjectFiles() } toolTipText: qsTr("Cerca file e @Mentions del progetto · @ nel messaggio") }
                                        KButton {
                                            objectName: "captureContextButton"
                                            iconName: "context"; subtle: !ambient.hasCapturedContext; quietSelection: ambient.hasCapturedContext; checked: ambient.hasCapturedContext
                                            implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13); implicitWidth: 32
                                            Accessible.name: qsTr("Cattura finestra o selezione attiva")
                                            toolTipText: qsTr("Cattura testo selezionato e finestra attiva del sistema")
                                            onClicked: ambient.captureContextNow()
                                        }
                                        KButton {
                                            objectName: "voiceDictationButton"
                                            iconName: ambient.recording ? "stop" : "mic"
                                            destructive: ambient.recording; subtle: !ambient.recording
                                            implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13); implicitWidth: 32
                                            enabled: !ambient.transcribing
                                            Accessible.name: ambient.recording ? qsTr("Ferma dettatura vocale") : qsTr("Avvia dettatura vocale")
                                            toolTipText: ambient.recording ? qsTr("Ferma e trascrivi con Whisper") : qsTr("Dettatura vocale (Whisper locale / API)")
                                            onClicked: ambient.toggleDictation("composer")
                                        }
                                        KButton {
                                            objectName: "openSpotlightButton"
                                            iconName: "spotlight"; subtle: true
                                            implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13); implicitWidth: 32
                                            Accessible.name: qsTr("Apri Kira Spotlight")
                                            toolTipText: qsTr("Apri Kira Spotlight (Alt+Spazio · globale: kira --spotlight)")
                                            onClicked: ambient.openSpotlight()
                                        }
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
                                            Layout.preferredWidth: Math.min(composerChips.tight ? 240 : 320, modelChipMetrics.advanceWidth + 72)
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
                                        KButton {
                                            id: contextWindowGauge; objectName: "contextWindowGauge"
                                            readonly property var gaugeData: {
                                                localization.revision
                                                return metricsPresenter.contextGauge(
                                                    workspace.usageData,
                                                    typeof chatContext !== "undefined" ? chatContext.metricsContext : ({}),
                                                    workspace.backend,
                                                    workspace.selectedModel,
                                                    prompt.text,
                                                    workspace.transcriptTokenEstimate,
                                                    workspace.selectedModelContextWindow
                                                )
                                            }
                                            readonly property color levelColor: gaugeData.level === "critical" ? appearance.colors.danger : gaugeData.level === "warning" ? appearance.colors.warning : appearance.colors.accent
                                            readonly property string displayLabel: composerChips.tight ? (gaugeData.short_label || "0%") : ((gaugeData.label || "0%") + (gaugeData.cost_badge ? " · " + gaugeData.cost_badge : ""))
                                            subtle: true; cornerRadius: 15
                                            implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                                            implicitWidth: Math.max(42, gaugeMetrics.advanceWidth + 22)
                                            font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 2)
                                            TextMetrics { id: gaugeMetrics; text: contextWindowGauge.displayLabel; font: contextWindowGauge.font }
                                            toolTipText: gaugeData.tooltip || qsTr("Finestra di contesto")
                                            Accessible.name: qsTr("Finestra di contesto: %1").arg(gaugeData.label || "0%")
                                            onClicked: metricsDialog.open()
                                            contentItem: Item {
                                                Text {
                                                    objectName: "contextWindowGaugeLabel"
                                                    anchors.fill: parent; leftPadding: 9; rightPadding: 9; bottomPadding: 3
                                                    text: contextWindowGauge.displayLabel
                                                    textFormat: Text.PlainText; elide: Text.ElideRight
                                                    font: contextWindowGauge.font
                                                    color: contextWindowGauge.gaugeData.level === "normal" ? appearance.colors.text : contextWindowGauge.levelColor
                                                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                                                }
                                                Rectangle {
                                                    anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                                                    anchors.leftMargin: 8; anchors.rightMargin: 8; anchors.bottomMargin: 4
                                                    height: 3; radius: 1.5
                                                    color: appearance.colors.border
                                                    Rectangle {
                                                        width: Math.max(3, parent.width * Math.min(1, Math.max(0, (contextWindowGauge.gaugeData.fill_percent || 0) / 100)))
                                                        height: parent.height; radius: 1.5
                                                        color: contextWindowGauge.levelColor
                                                    }
                                                }
                                            }
                                        }
                                        KButton { objectName: "projectQueueButton"; implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13); cornerRadius: 15; iconName: "queue"; text: composerChips.tight ? (workbench.pendingCount > 0 ? String(workbench.pendingCount) : "") : qsTr("Coda %1").arg(workbench.pendingCount); subtle: true; Accessible.name: (workbench.pendingCount === 1 ? qsTr("Coda del progetto: %1 richiesta") : qsTr("Coda del progetto: %1 richieste")).arg(workbench.pendingCount); toolTipText: qsTr("Coda del progetto · controlla, modifica e riordina le richieste"); onClicked: workbenchDialog.openSection("queue") }
                                        KButton { objectName: "chatToolsButton"; implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13); iconName: "more"; subtle: true; implicitWidth: 34; Accessible.name: qsTr("Strumenti della chat"); toolTipText: qsTr("Cronologia, obiettivo, modifiche e gestione chat"); onClicked: { window.commandStart = -1; window.openActions("") } }
                                        KButton {
                                            objectName: "queueComposerButton"; text: qsTr("Accoda")
                                            subtle: true; implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                                            visible: workspace.busy || teams.busy || assistant.active || workbench.guidanceRetryAvailable
                                            toolTipText: qsTr("Accoda una richiesta separata; non cambia il turno attivo")
                                            enabled: workbench.composerReady && workbench.modeAvailable && !workbench.sessionArchived && !prompt.attachmentBusy && (prompt.text.trim().length > 0 || workspace.draftImages.length > 0 || mentions.hasActiveChips) && (workspace.draftImages.length === 0 || workspace.imageInputsEnabled) && workspace.selectedModel.length > 0
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
                                            enabled: (workbench.canSubmit || workbench.goalCommandReady(prompt.text)) && !prompt.attachmentBusy && (prompt.text.trim().length > 0 || workspace.draftImages.length > 0 || mentions.hasActiveChips) && (workspace.draftImages.length === 0 || workspace.imageInputsEnabled) && workspace.selectedModel.length > 0
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
                                                readonly property var availableBackendIds: {
                                                    workspace.disabledBackends
                                                    const enabled = workspace.enabledBackends || []
                                                    const all = ["ollama", "codex", "openai_chatgpt", "deepseek_account", "deepseek_web", "claude_code", "google_cloud", "openrouter"]
                                                    const list = enabled.length > 0 ? all.filter(function(b) { return enabled.indexOf(b) >= 0 }) : all
                                                    if (workspace.backend && list.indexOf(workspace.backend) < 0) list.push(workspace.backend)
                                                    return list
                                                }
                                                Layout.preferredWidth: ["openai_chatgpt", "deepseek_account", "deepseek_web", "claude_code", "google_cloud", "openrouter"].indexOf(workspace.backend) >= 0 ? (chatColumn.width < 650 ? 160 : 255) : 110; implicitHeight: 35
                                                model: availableBackendIds.map(function(b) { return kformat.providerLabel(b) })
                                                currentIndex: availableBackendIds.indexOf(workspace.backend)
                                                onActivated: { workspace.setBackend(availableBackendIds[currentIndex]); currentIndex = Qt.binding(function() { return backendBox.availableBackendIds.indexOf(workspace.backend) }) }
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
                                ChatToolButton { objectName: "chatWebButton"; label: qsTr("Browser"); iconName: "browser"; enabled: workspace.web.available; checked: workspace.web.paneVisible; quietSelection: true; onClicked: { workspace.studio.hideStudio(); workspace.canvas.hideCanvas(); workspace.web.showBrowser() } toolTipText: qsTr("Apri il browser integrato") }
                                ChatToolButton { objectName: "chatCanvasButton"; label: qsTr("Canvas"); iconName: "studio"; visible: !workspace.web.paneVisible; enabled: workspace.connected; checked: workspace.canvas.paneVisible; quietSelection: true; onClicked: { workspace.web.hideBrowser(); workspace.studio.hideStudio(); workspace.canvas.toggleCanvas() } toolTipText: qsTr("Apri il Canvas interattivo (Mermaid, HTML/UI, SVG, Diff)") }
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
                                        border.width: teamStatusButton.visualFocus ? 2 : teamStatusButton.paused ? 1 : 0
                                        border.color: teamStatusButton.visualFocus ? appearance.colors.accent
                                            : Qt.rgba(appearance.colors.warning.r, appearance.colors.warning.g, appearance.colors.warning.b, 0.25)
                                    }
                                    onClicked: { if (assistantTeamPanel.hasTeamContext) assistantTeamPanel.toggleDetails(); else assistant.configureTeam() }
                                    toolTipText: paused ? stateText : assistantTeamPanel.hasDelegation ? qsTr("Team · %1").arg(assistant.current.label) : qsTr("Configura un team per questo progetto")
                                    Accessible.name: stateText
                                }
                                ChatToolButton { objectName: "inputOpen"; label: qsTr("Domande"); iconName: "questions"; badge: inputs.pendingCount; primary: inputs.pendingCount > 0; destructive: inputs.error.length > 0; onClicked: inputDialog.open(); toolTipText: inputs.error || qsTr("Domande · Ctrl+Shift+Q") }
                                ChatToolButton { objectName: "chatTriggersButton"; visible: workbench.triggerSummaryBadge.length > 0; label: workbench.triggerSummaryBadge; iconName: "goal"; badge: workbench.activeTriggerCount; primary: workbench.activeTriggerCount > 0; onClicked: workbenchDialog.openSection("goal"); toolTipText: workbench.triggerSummaryBadge }
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
                        CanvasPane {
                            visible: workspace.canvas.paneVisible
                            SplitView.preferredWidth: Math.max(520, browserSplit.width * 0.58)
                            SplitView.minimumWidth: 380; SplitView.fillWidth: window.canvasFocused
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
                            visible: window.detailsVisible && window.width >= 1120 && !workspace.web.paneVisible && !workspace.studio.paneVisible && !workspace.canvas.paneVisible && !panelHost.activePanel
                            Loader { anchors.fill: parent; anchors.margins: 0; sourceComponent: projectDetails }
                        }
                    }
                }
                ScrollView {
                    id: modelsScroll; clip: true; contentWidth: availableWidth
                    ColumnLayout {
                        id: modelsPage
                        readonly property real sideMargin: modelsScroll.availableWidth < 900 ? 24 : Math.max(28, (modelsScroll.availableWidth - 960) / 2)
                        // With a chat open (or when inspecting a disabled provider), the provider buttons
                        // only show and configure a provider; "Usa modello" is what moves the chat to it.
                        property string viewedBackend: ""
                        readonly property bool foreign: viewedBackend.length > 0 && viewedBackend !== workspace.backend && (workspace.sessionId.length > 0 || !providerEnabled(viewedBackend))
                        readonly property string shownBackend: foreign ? viewedBackend : workspace.backend
                        readonly property bool shownProviderEnabled: providerEnabled(shownBackend)
                        readonly property var viewedCatalog: { workspace.models; return foreign ? workspace.catalogFor(viewedBackend) : ({}) }
                        function providerEnabled(backend) {
                            const disabled = workspace.disabledBackends || []
                            return disabled.indexOf(backend) < 0
                        }
                        function show(backend) {
                            if (!providerEnabled(backend)) {
                                viewedBackend = backend
                                return
                            }
                            if (backend === "google_cloud") googleAccount.refresh()
                            if (backend === "openrouter") openrouterAccount.refresh()
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
                                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Hub Provider e Modelli"); color: appearance.colors.text; font.pixelSize: appearance.uiFont.pixelSize + 11; font.weight: Font.DemiBold; font.letterSpacing: -0.2; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; textFormat: Text.PlainText; text: qsTr("Gestisci i provider locali e cloud integrati in Kira, abilita o disattiva ciascun servizio e scegli il modello attivo per chat, agenti e team."); color: appearance.colors.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            }
                            KButton { objectName: "modelsRefreshButton"; text: qsTr("Aggiorna"); iconName: "refresh"; enabled: modelsPage.shownProviderEnabled; onClicked: workspace.discover(modelsPage.shownBackend); toolTipText: qsTr("Aggiorna il catalogo del provider selezionato") }
                        }
                        // Providers are a segmented row; the selected one carries the page.
                        Flow {
                            Layout.fillWidth: true; Layout.leftMargin: modelsPage.sideMargin; Layout.rightMargin: modelsPage.sideMargin; spacing: 6
                            KButton { objectName: "ollamaBackendButton"; text: "Ollama" + (modelsPage.providerEnabled("ollama") ? "" : " · Off"); opacity: modelsPage.providerEnabled("ollama") || checked ? 1.0 : 0.55; cornerRadius: 17; enabled: !workspace.busy && !assistant.active; checked: modelsPage.shownBackend === "ollama"; onClicked: modelsPage.show("ollama") }
                            KButton { objectName: "codexBackendButton"; text: "Codex" + (modelsPage.providerEnabled("codex") ? "" : " · Off"); opacity: modelsPage.providerEnabled("codex") || checked ? 1.0 : 0.55; cornerRadius: 17; enabled: !workspace.busy && !assistant.active; checked: modelsPage.shownBackend === "codex"; onClicked: modelsPage.show("codex") }
                            KButton { objectName: "directBackendButton"; text: qsTr("OpenAI · ChatGPT") + (modelsPage.providerEnabled("openai_chatgpt") ? "" : " · Off"); opacity: modelsPage.providerEnabled("openai_chatgpt") || checked ? 1.0 : 0.55; cornerRadius: 17; enabled: !workspace.busy && !assistant.active; checked: modelsPage.shownBackend === "openai_chatgpt"; onClicked: modelsPage.show("openai_chatgpt") }
                            KButton { objectName: "deepseekBackendButton"; text: qsTr("DeepSeek API") + (modelsPage.providerEnabled("deepseek_account") ? "" : " · Off"); opacity: modelsPage.providerEnabled("deepseek_account") || checked ? 1.0 : 0.55; cornerRadius: 17; enabled: !workspace.busy && !assistant.active; checked: modelsPage.shownBackend === "deepseek_account"; onClicked: modelsPage.show("deepseek_account") }
                            KButton { objectName: "deepseekWebBackendButton"; text: qsTr("DeepSeek Web") + (modelsPage.providerEnabled("deepseek_web") ? "" : " · Off"); opacity: modelsPage.providerEnabled("deepseek_web") || checked ? 1.0 : 0.55; cornerRadius: 17; enabled: !workspace.busy && !assistant.active; checked: modelsPage.shownBackend === "deepseek_web"; onClicked: modelsPage.show("deepseek_web") }
                            KButton { objectName: "claudeCodeBackendButton"; text: qsTr("Claude Code") + (modelsPage.providerEnabled("claude_code") ? "" : " · Off"); opacity: modelsPage.providerEnabled("claude_code") || checked ? 1.0 : 0.55; cornerRadius: 17; enabled: !workspace.busy && !assistant.active; checked: modelsPage.shownBackend === "claude_code"; onClicked: modelsPage.show("claude_code") }
                            KButton { objectName: "googleCloudBackendButton"; text: qsTr("Google Cloud · Vertex AI") + (modelsPage.providerEnabled("google_cloud") ? "" : " · Off"); opacity: modelsPage.providerEnabled("google_cloud") || checked ? 1.0 : 0.55; cornerRadius: 17; enabled: !workspace.busy && !assistant.active; checked: modelsPage.shownBackend === "google_cloud"; onClicked: modelsPage.show("google_cloud") }
                            KButton { objectName: "openrouterBackendButton"; text: qsTr("OpenRouter") + (modelsPage.providerEnabled("openrouter") ? "" : " · Off"); opacity: modelsPage.providerEnabled("openrouter") || checked ? 1.0 : 0.55; cornerRadius: 17; enabled: !workspace.busy && !assistant.active; checked: modelsPage.shownBackend === "openrouter"; onClicked: modelsPage.show("openrouter") }
                        }
                        // One status line: what the provider reported and the actions that follow.
                        Rectangle {
                            objectName: "providerStatusCard"
                            Layout.fillWidth: true; Layout.leftMargin: modelsPage.sideMargin; Layout.rightMargin: modelsPage.sideMargin
                            implicitHeight: providerStatusRow.implicitHeight + 24; radius: 12
                            color: appearance.colors.surface; border.color: appearance.colors.border
                            RowLayout {
                                id: providerStatusRow; anchors.fill: parent; anchors.margins: 12; anchors.leftMargin: 16; spacing: 12
                                Rectangle { objectName: "providerStatusDot"; width: 10; height: 10; radius: 5; color: !modelsPage.shownProviderEnabled ? muted : modelsPage.foreign ? (modelsPage.viewedCatalog.available ? appearance.colors.success : modelsPage.viewedCatalog.error ? appearance.colors.warning : muted) : workspace.providerState === "error" ? appearance.colors.danger : workspace.providerState === "ok" ? appearance.colors.success : workspace.providerState === "warning" ? appearance.colors.warning : muted; Layout.alignment: Qt.AlignVCenter }
                                ReadText { objectName: "providerStatusText"; text: !modelsPage.shownProviderEnabled ? qsTr("%1 è disattivato · Riattivalo con il pulsante a destra per utilizzarlo nelle chat e nei team.").arg(kformat.providerLabel(modelsPage.shownBackend)) : !modelsPage.foreign ? workspace.providerText : modelsPage.viewedCatalog.available ? qsTr("%1 modelli disponibili. Questa chat usa %2: con «Usa modello» la continui con %3.").arg((modelsPage.viewedCatalog.models || []).length).arg(kformat.providerLabel(workspace.backend)).arg(kformat.providerLabel(modelsPage.viewedBackend)) : modelsPage.viewedCatalog.error ? qsTr("%1 non è ancora pronto: %2").arg(kformat.providerLabel(modelsPage.viewedBackend)).arg(modelsPage.viewedCatalog.error) : qsTr("Caricamento dei modelli…"); Layout.fillWidth: true; color: !modelsPage.shownProviderEnabled ? muted : !modelsPage.foreign && workspace.providerUnavailable ? appearance.colors.danger : appearance.colors.text; wrapMode: Text.Wrap }
                                KButton { objectName: "codexLoginButton"; text: qsTr("Accedi con Codex"); iconName: "external"; visible: modelsPage.shownBackend === "codex" && modelsPage.shownProviderEnabled; onClicked: workspace.login() }
                                KButton {
                                    objectName: "providerEnableToggleButton"
                                    text: modelsPage.shownProviderEnabled ? qsTr("Disattiva provider") : qsTr("Attiva provider")
                                    iconName: modelsPage.shownProviderEnabled ? "close" : "check"
                                    primary: !modelsPage.shownProviderEnabled
                                    subtle: modelsPage.shownProviderEnabled
                                    enabled: !workspace.busy && !assistant.active && (!modelsPage.shownProviderEnabled || (workspace.enabledBackends || []).length > 1)
                                    toolTipText: modelsPage.shownProviderEnabled
                                        ? ((workspace.enabledBackends || []).length > 1 ? qsTr("Disattiva %1 e nascondilo dai selettori rapidi di chat e team").arg(kformat.providerLabel(modelsPage.shownBackend)) : qsTr("Almeno un provider deve rimanere attivo"))
                                        : qsTr("Riattiva %1 per utilizzarlo nelle chat e nei team").arg(kformat.providerLabel(modelsPage.shownBackend))
                                    Accessible.name: text
                                    onClicked: {
                                        const target = modelsPage.shownBackend
                                        const willEnable = !modelsPage.shownProviderEnabled
                                        if (workspace.setProviderEnabled(target, willEnable)) {
                                            if (willEnable) {
                                                modelsPage.show(target)
                                            } else {
                                                modelsPage.viewedBackend = target
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        DirectAccountPane { objectPrefix: "modelsChatGPT"; visible: modelsPage.shownProviderEnabled && modelsPage.shownBackend === "openai_chatgpt"; Layout.leftMargin: modelsPage.sideMargin; Layout.rightMargin: modelsPage.sideMargin }
                        DeepSeekAccountPane { visible: modelsPage.shownProviderEnabled && modelsPage.shownBackend === "deepseek_account"; Layout.leftMargin: modelsPage.sideMargin; Layout.rightMargin: modelsPage.sideMargin }
                        DeepSeekWebPane { objectName: "deepseekWebPane"; visible: modelsPage.shownProviderEnabled && modelsPage.shownBackend === "deepseek_web"; Layout.leftMargin: modelsPage.sideMargin; Layout.rightMargin: modelsPage.sideMargin }
                        GoogleCloudPane { objectName: "googleCloudPane"; visible: modelsPage.shownProviderEnabled && modelsPage.shownBackend === "google_cloud"; Layout.leftMargin: modelsPage.sideMargin; Layout.rightMargin: modelsPage.sideMargin }
                        OpenRouterPane { objectName: "openrouterPane"; visible: modelsPage.shownProviderEnabled && modelsPage.shownBackend === "openrouter"; Layout.leftMargin: modelsPage.sideMargin; Layout.rightMargin: modelsPage.sideMargin }
                        ReadText { objectName: "claudeCodePersonalNotice"; visible: modelsPage.shownProviderEnabled && modelsPage.shownBackend === "claude_code"; text: qsTr("Claude Code usa l'accesso già fatto nel tuo Claude Code locale: Kira non legge né conserva le credenziali. Solo per uso personale: non distribuire Kira con questo provider. I permessi del progetto decidono gli strumenti di Claude Code; Kira blocca i percorsi fuori dal progetto e registra le azioni osservate."); Layout.fillWidth: true; Layout.leftMargin: modelsPage.sideMargin; Layout.rightMargin: modelsPage.sideMargin; color: muted; wrapMode: Text.Wrap }
                        GridLayout {
                            visible: modelsPage.shownProviderEnabled
                            Layout.fillWidth: true; Layout.leftMargin: modelsPage.sideMargin; Layout.rightMargin: modelsPage.sideMargin
                            columns: modelsScroll.availableWidth - modelsPage.sideMargin * 2 < 700 ? 1 : 2; columnSpacing: 14; rowSpacing: 14
                            Repeater {
                                model: !modelsPage.shownProviderEnabled ? [] : modelsPage.foreign ? (modelsPage.viewedCatalog.models || []) : workspace.models
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
                                        Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: modelsPage.shownBackend === "claude_code" ? qsTr("Agente Claude Code · azioni native osservate da Kira") : modelsPage.shownBackend === "deepseek_web" ? qsTr("Strumenti mediati da Kira · protocollo sperimentale") : modelCard.modelData.tools_verified ? qsTr("Strumenti verificati") : modelCard.modelData.tools_metadata ? qsTr("Supporto strumenti dichiarato · verifica al primo uso") : modelsPage.shownBackend === "codex" ? qsTr("Agente Codex · l'accesso va confermato con un turno reale") : ["openai_chatgpt", "deepseek_account", "google_cloud", "openrouter"].indexOf(modelsPage.shownBackend) >= 0 ? qsTr("Strumenti gestiti da Kira · verifica al primo uso") : qsTr("Conversazione"); color: appearance.colors.textSecondary; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1); Layout.fillWidth: true; wrapMode: Text.Wrap }
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
                                            KButton { objectName: "useModel_" + modelCard.modelData.id; visible: !modelCard.selected; text: qsTr("Usa modello"); enabled: !workspace.busy && !assistant.active && modelsPage.shownProviderEnabled; onClicked: { if (modelsPage.foreign) { providerSwitch.chooseModel(modelsPage.viewedBackend, modelCard.modelData.id); return } workspace.setModel(modelCard.modelData.id); if (hasProject) window.page = 0 } }
                                        }
                                    }
                                }
                            }
                        }
                        // Raw provider data stay available, folded by default.
                        Rectangle {
                            visible: !modelsPage.foreign && modelsPage.shownProviderEnabled
                            Layout.fillWidth: true; Layout.leftMargin: modelsPage.sideMargin; Layout.rightMargin: modelsPage.sideMargin; Layout.bottomMargin: 30
                            implicitHeight: catalogColumn.implicitHeight + 2; radius: 12
                            color: appearance.colors.surface; border.color: appearance.colors.border
                            ColumnLayout {
                                id: catalogColumn; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; spacing: 0
                                KButton {
                                    id: catalogToggle; objectName: "catalogToggle"
                                    Layout.fillWidth: true; implicitHeight: 42
                                    text: qsTr("Diagnostica e catalogo grezzo del provider"); iconName: checked ? "chevron-down" : "chevron-right"
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
    }

    Popup {
        id: appTopMenuPopup
        objectName: "appTopMenuPopup"
        width: 280 + Math.max(0, appearance.uiFont.pixelSize - 13) * 8
        padding: 6
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onClosed: window.activeTopMenu = ""
        background: Rectangle {
            color: appearance.colors.surfaceAlt
            radius: 10
            border.color: appearance.colors.border
            border.width: 1
        }
        contentItem: ColumnLayout {
            spacing: 2
            Repeater {
                model: window.topMenuItems(window.activeTopMenu)
                delegate: Item {
                    id: topMenuRow
                    required property var modelData
                    readonly property bool isSep: Boolean(modelData.separator)
                    Layout.fillWidth: true
                    implicitHeight: isSep ? 9 : (30 + Math.max(0, appearance.uiFont.pixelSize - 13))
                    Rectangle {
                        visible: topMenuRow.isSep
                        anchors.left: parent.left; anchors.right: parent.right
                        anchors.leftMargin: 8; anchors.rightMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                        height: 1
                        color: appearance.colors.border
                    }
                    KButton {
                        id: topMenuItemBtn
                        visible: !topMenuRow.isSep
                        objectName: topMenuRow.modelData.id || ""
                        anchors.fill: parent
                        text: topMenuRow.modelData.label || ""
                        iconName: topMenuRow.modelData.icon || ""
                        enabled: topMenuRow.modelData.enabled !== false
                        destructive: Boolean(topMenuRow.modelData.destructive)
                        subtle: true
                        leftAligned: true
                        Accessible.name: text
                        contentItem: Item {
                            KIcon {
                                visible: (topMenuRow.modelData.icon || "").length > 0
                                name: topMenuRow.modelData.icon || ""
                                ink: topMenuItemBtn.foreground
                                width: 16; height: 16
                                anchors.left: parent.left; anchors.leftMargin: 10
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            Text {
                                anchors.fill: parent
                                leftPadding: 34
                                rightPadding: topMenuShortcut.visible ? topMenuShortcut.implicitWidth + 18 : 12
                                text: topMenuItemBtn.text
                                font: topMenuItemBtn.font
                                color: topMenuItemBtn.foreground
                                textFormat: Text.PlainText
                                elide: Text.ElideRight
                                verticalAlignment: Text.AlignVCenter
                            }
                            Text {
                                id: topMenuShortcut
                                visible: Boolean(topMenuRow.modelData.shortcut)
                                anchors.right: parent.right; anchors.rightMargin: 10
                                anchors.verticalCenter: parent.verticalCenter
                                text: topMenuRow.modelData.shortcut || ""
                                font.family: appearance.codeFont.family
                                font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 2)
                                color: appearance.colors.muted
                                textFormat: Text.PlainText
                            }
                        }
                        onClicked: {
                            const fn = topMenuRow.modelData.action
                            appTopMenuPopup.close()
                            if (typeof fn === "function") fn()
                        }
                    }
                }
            }
        }
    }

    Popup {
        id: chatFilterMenu
        objectName: "chatFilterMenu"
        width: 210 + Math.max(0, appearance.uiFont.pixelSize - 13) * 6
        padding: 6
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle {
            color: appearance.colors.surfaceAlt
            radius: 10
            border.color: appearance.colors.border
            border.width: 1
        }
        contentItem: ColumnLayout {
            spacing: 2
            Repeater {
                model: [
                    { key: "all", label: qsTr("Tutte le chat"), icon: "projects" },
                    { key: "pinned", label: qsTr("Fissate in alto"), icon: "pin" },
                    { key: "unread", label: qsTr("Non lette"), icon: "unread" },
                    { key: "archived", label: qsTr("Archiviate"), icon: "archive" }
                ]
                delegate: KButton {
                    required property var modelData
                    objectName: "chatFilter_" + modelData.key
                    Layout.fillWidth: true
                    implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                    text: modelData.label
                    iconName: workspace.sessionFilter === modelData.key ? "check" : modelData.icon
                    checked: workspace.sessionFilter === modelData.key
                    quietSelection: true
                    subtle: true
                    leftAligned: true
                    Accessible.name: modelData.label
                    onClicked: {
                        workspace.setSessionFilter(modelData.key)
                        chatFilterMenu.close()
                    }
                }
            }
        }
    }

    Popup {
        id: projectContextMenu
        objectName: "projectContextMenu"
        width: 240 + Math.max(0, appearance.uiFont.pixelSize - 13) * 6
        padding: 6
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle {
            color: appearance.colors.surfaceAlt
            radius: 10
            border.color: appearance.colors.border
            border.width: 1
        }
        contentItem: ColumnLayout {
            spacing: 2
            KButton {
                objectName: "projectMenuNewChat"
                Layout.fillWidth: true
                implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                text: qsTr("Nuova chat")
                iconName: "plus"; subtle: true; leftAligned: true
                onClicked: {
                    const pid = window.projectMenuId
                    projectContextMenu.close()
                    workspace.newChatInProject(pid)
                    window.page = 0
                }
            }
            KButton {
                objectName: "projectMenuOpenFolder"
                Layout.fillWidth: true
                implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                text: qsTr("Apri cartella del progetto")
                iconName: "folder"; subtle: true; leftAligned: true
                onClicked: {
                    const pid = window.projectMenuId
                    projectContextMenu.close()
                    if (workspace.project.id !== pid) workspace.openProject(pid)
                    workspace.openProjectFolder()
                }
            }
            KButton {
                objectName: "projectMenuPermissions"
                Layout.fillWidth: true
                implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                text: qsTr("Memoria e permessi…")
                iconName: "lock"; subtle: true; leftAligned: true
                onClicked: {
                    const pid = window.projectMenuId
                    projectContextMenu.close()
                    if (workspace.project.id !== pid) workspace.openProject(pid)
                    memoryDialog.open()
                }
            }
            Rectangle { Layout.fillWidth: true; Layout.leftMargin: 8; Layout.rightMargin: 8; height: 1; color: appearance.colors.border }
            KButton {
                objectName: "projectMenuCopyPath"
                Layout.fillWidth: true
                implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                text: qsTr("Copia percorso progetto")
                iconName: "copy"; subtle: true; leftAligned: true
                onClicked: {
                    const rootPath = window.projectMenuRoot
                    projectContextMenu.close()
                    workspace.copy(rootPath)
                }
            }
            Rectangle { Layout.fillWidth: true; Layout.leftMargin: 8; Layout.rightMargin: 8; height: 1; color: appearance.colors.border }
            KButton {
                objectName: "projectMenuDelete"
                Layout.fillWidth: true
                implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                text: qsTr("Elimina progetto…")
                iconName: "trash"; subtle: true; leftAligned: true; destructive: true
                Accessible.name: text
                onClicked: {
                    const proj = { id: window.projectMenuId, name: window.projectMenuName, root: window.projectMenuRoot }
                    projectContextMenu.close()
                    window.promptDeleteProject(proj)
                }
            }
        }
    }

    Popup {
        id: chatContextMenu
        objectName: "chatContextMenu"
        width: 245 + Math.max(0, appearance.uiFont.pixelSize - 13) * 6
        padding: 6
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle {
            color: appearance.colors.surfaceAlt
            radius: 10
            border.color: appearance.colors.border
            border.width: 1
        }
        contentItem: ColumnLayout {
            spacing: 2
            KButton {
                objectName: "chatMenuRename"
                Layout.fillWidth: true
                implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                text: qsTr("Rinomina")
                iconName: "rename"; subtle: true; leftAligned: true
                Accessible.name: text
                onClicked: {
                    const sid = window.chatMenuSessionId
                    const title = window.chatMenuTitle
                    chatContextMenu.close()
                    window.openRenameChat(sid, title)
                }
            }
            KButton {
                objectName: "chatMenuPin"
                Layout.fillWidth: true
                implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                text: window.chatMenuPinned ? qsTr("Rimuovi fissaggio") : qsTr("Fissa")
                iconName: "pin"; subtle: true; leftAligned: true
                Accessible.name: text
                onClicked: {
                    const sid = window.chatMenuSessionId
                    const nextPinned = !window.chatMenuPinned
                    chatContextMenu.close()
                    workspace.pinSession(sid, nextPinned)
                }
            }
            KButton {
                objectName: "chatMenuUnread"
                Layout.fillWidth: true
                implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                text: window.chatMenuUnread ? qsTr("Segna come letta") : qsTr("Segna come non letta")
                iconName: "unread"; subtle: true; leftAligned: true
                Accessible.name: text
                onClicked: {
                    const sid = window.chatMenuSessionId
                    const nextUnread = !window.chatMenuUnread
                    chatContextMenu.close()
                    workspace.markSessionUnread(sid, nextUnread)
                }
            }
            Rectangle { Layout.fillWidth: true; Layout.leftMargin: 8; Layout.rightMargin: 8; height: 1; color: appearance.colors.border }
            KButton {
                id: chatMenuCopyToggleBtn
                objectName: "chatMenuCopyToggle"
                Layout.fillWidth: true
                implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                text: qsTr("Copia")
                iconName: "copy"; subtle: true; leftAligned: true
                checked: window.chatMenuCopyExpanded; quietSelection: true
                Accessible.name: text
                contentItem: Item {
                    KIcon {
                        name: "copy"
                        ink: chatMenuCopyToggleBtn.foreground
                        width: 16; height: 16
                        anchors.left: parent.left; anchors.leftMargin: 10
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        anchors.fill: parent
                        leftPadding: 34; rightPadding: 26
                        text: chatMenuCopyToggleBtn.text
                        font: chatMenuCopyToggleBtn.font
                        color: chatMenuCopyToggleBtn.foreground
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        verticalAlignment: Text.AlignVCenter
                    }
                    KIcon {
                        name: window.chatMenuCopyExpanded ? "chevron-down" : "chevron-right"
                        ink: appearance.colors.muted
                        width: 14; height: 14
                        anchors.right: parent.right; anchors.rightMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                onClicked: window.chatMenuCopyExpanded = !window.chatMenuCopyExpanded
            }
            ColumnLayout {
                visible: window.chatMenuCopyExpanded
                Layout.fillWidth: true
                Layout.leftMargin: 14
                spacing: 1
                KButton {
                    objectName: "chatMenuCopyMarkdown"
                    Layout.fillWidth: true
                    implicitHeight: 28 + Math.max(0, appearance.uiFont.pixelSize - 13)
                    text: qsTr("Copia trascrizione Markdown")
                    iconName: "document"; subtle: true; leftAligned: true
                    Accessible.name: text
                    onClicked: {
                        const sid = window.chatMenuSessionId
                        chatContextMenu.close()
                        workspace.copySessionItem(sid, "markdown")
                    }
                }
                KButton {
                    objectName: "chatMenuCopyTitle"
                    Layout.fillWidth: true
                    implicitHeight: 28 + Math.max(0, appearance.uiFont.pixelSize - 13)
                    text: qsTr("Copia titolo")
                    iconName: "labels"; subtle: true; leftAligned: true
                    Accessible.name: text
                    onClicked: {
                        const sid = window.chatMenuSessionId
                        chatContextMenu.close()
                        workspace.copySessionItem(sid, "title")
                    }
                }
                KButton {
                    objectName: "chatMenuCopyId"
                    Layout.fillWidth: true
                    implicitHeight: 28 + Math.max(0, appearance.uiFont.pixelSize - 13)
                    text: qsTr("Copia ID sessione")
                    iconName: "code"; subtle: true; leftAligned: true
                    Accessible.name: text
                    onClicked: {
                        const sid = window.chatMenuSessionId
                        chatContextMenu.close()
                        workspace.copySessionItem(sid, "id")
                    }
                }
                KButton {
                    objectName: "chatMenuCopyPath"
                    Layout.fillWidth: true
                    implicitHeight: 28 + Math.max(0, appearance.uiFont.pixelSize - 13)
                    text: qsTr("Copia percorso progetto")
                    iconName: "link"; subtle: true; leftAligned: true
                    Accessible.name: text
                    onClicked: {
                        const sid = window.chatMenuSessionId
                        chatContextMenu.close()
                        workspace.copySessionItem(sid, "path")
                    }
                }
            }
            KButton {
                objectName: "chatMenuBranch"
                Layout.fillWidth: true
                implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                text: qsTr("Dirama chat")
                iconName: "branch"; subtle: true; leftAligned: true
                Accessible.name: text
                onClicked: {
                    const sid = window.chatMenuSessionId
                    chatContextMenu.close()
                    workspace.branchSession(sid)
                }
            }
            KButton {
                objectName: "chatMenuExport"
                Layout.fillWidth: true
                implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                text: qsTr("Esporta Markdown…")
                iconName: "export"; subtle: true; leftAligned: true
                Accessible.name: text
                onClicked: {
                    window.exportChatSessionId = window.chatMenuSessionId
                    chatContextMenu.close()
                    chatMarkdownExport.open()
                }
            }
            Rectangle { Layout.fillWidth: true; Layout.leftMargin: 8; Layout.rightMargin: 8; height: 1; color: appearance.colors.border }
            KButton {
                objectName: "chatMenuArchive"
                Layout.fillWidth: true
                implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                text: window.chatMenuArchived ? qsTr("Ripristina dall'archivio") : qsTr("Archivia")
                iconName: "archive"; subtle: true; leftAligned: true
                Accessible.name: text
                onClicked: {
                    const sid = window.chatMenuSessionId
                    const nextArch = !window.chatMenuArchived
                    chatContextMenu.close()
                    workspace.archiveSession(sid, nextArch)
                }
            }
            KButton {
                objectName: "chatMenuDelete"
                Layout.fillWidth: true
                implicitHeight: 30 + Math.max(0, appearance.uiFont.pixelSize - 13)
                text: qsTr("Elimina")
                iconName: "trash"; subtle: true; leftAligned: true; destructive: true
                Accessible.name: text
                onClicked: {
                    const sid = window.chatMenuSessionId
                    chatContextMenu.close()
                    workspace.deleteSession(sid)
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
                                        delegate: KButton { required property var modelData; Layout.fillWidth: true; text: "↗  " + (modelData.rel_path || String(modelData.path).split("/").pop()); onClicked: workspace.openArtifact(modelData.path); toolTipText: modelData.path }
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
    ProjectFileDialog {
        id: projectFileDialog; parent: Overlay.overlay
        onMentionSelected: function(kind, value, token) {
            if (window.mentionStart >= 0 && prompt.text.slice(window.mentionStart, window.mentionStart + 1) === "@") {
                prompt.remove(window.mentionStart, window.mentionStart + 1)
            }
            window.mentionStart = -1
            mentions.chooseUniversalItem(kind, value, token)
            prompt.forceActiveFocus()
        }
        onClosed: window.mentionStart = -1
    }
    // Covers the window, popups included, until the startup stages are done.
    KiraLoadingScreen { parent: Overlay.overlay }
    UpdateDialog { id: updateDialog }
    WorkbenchDialog { id: workbenchDialog; parent: Overlay.overlay }
    KiraFileDialog {
        id: chatMarkdownExport; mode: "save"; purpose: "export"; title: qsTr("Esporta chat")
        nameFilters: [qsTr("Markdown (*.md)")]; defaultSuffix: "md"; suggestedName: window.exportName(qsTr("chat"))
        onAccepted: {
            const targetSid = window.exportChatSessionId || workspace.sessionId
            window.exportChatSessionId = ""
            if (targetSid && targetSid !== workspace.sessionId) workspace.exportSessionMarkdown(targetSid, selectedFile.toString())
            else workbench.exportChat(selectedFile.toString())
        }
    }
    WorkspaceDialog { id: resumeDialog; parent: Overlay.overlay; inspectorKey: "riprendi" }
    ActionDialog {
        id: actionDialog; parent: Overlay.overlay
        onProjectSelected: { window.page = 0; window.followOutput = true }
        onSoulRequested: function(soulId, viewMode) {
            window.page = 0
            souls.openSoulView(soulId, viewMode)
            soulDialog.open()
        }
        onResearchRequested: function(projectId, recordId) {
            window.page = 0
            if (projectId && (!workspace.project || workspace.project.id !== projectId)) {
                workspace.openProject(projectId)
            }
            researchDialog.open()
        }
        onCanvasRequested: function(projectId, artifactId) {
            window.page = 0
            if (projectId && (!workspace.project || workspace.project.id !== projectId)) {
                workspace.openProject(projectId)
            }
            workspace.canvas.openArtifactById(artifactId)
        }
    }
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
                KButton {
                    objectName: "projectPermissionDelete"
                    text: qsTr("Elimina progetto…")
                    iconName: "trash"; subtle: true; destructive: true
                    enabled: workspace.connected && !workspace.busy && !assistant.active && !teams.busy && !!(workspace.project && workspace.project.id)
                    onClicked: {
                        const proj = workspace.project
                        memoryDialog.close()
                        window.promptDeleteProject(proj)
                    }
                }
                Item { Layout.fillWidth: true }
                KButton { text: qsTr("Chiudi"); subtle: true; onClicked: memoryDialog.close() }
                KButton { objectName: "projectPermissionSave"; text: qsTr("Salva"); primary: true; enabled: workspace.connected; onClicked: workspace.updateProject(editInstructions.text, ["read_only", "project_write", "trusted_host", "full_access"][editPermission.currentIndex], memoryInput.text) }
            }
        }
    }
    Dialog {
        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
        id: deleteProjectDialog; objectName: "deleteProjectDialog"; title: qsTr("Elimina progetto da Kira"); modal: true
        anchors.centerIn: parent; width: Math.min(540, window.width - 60)
        standardButtons: Dialog.NoButton
        background: Rectangle { color: appearance.colors.surfaceAlt; radius: 12; border.color: appearance.colors.border }
        ColumnLayout {
            width: parent.width; spacing: 12
            Text {
                font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: Font.DemiBold; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
                textFormat: Text.PlainText
                text: qsTr("Rimuovere «%1» da Kira?").arg(window.deleteProjectName)
                color: appearance.colors.text; wrapMode: Text.WordWrap; Layout.fillWidth: true
            }
            Text {
                font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
                textFormat: Text.PlainText
                text: qsTr("Verranno rimosse le chat, la memoria e le impostazioni del progetto salvate in Kira. La cartella e tutti i tuoi file su disco non verranno toccati.")
                color: muted; wrapMode: Text.WordWrap; Layout.fillWidth: true
            }
            Rectangle {
                visible: window.deleteProjectRoot.length > 0
                Layout.fillWidth: true; radius: 8
                color: appearance.colors.surface; border.color: appearance.colors.border; border.width: 1
                implicitHeight: deleteRootLabel.implicitHeight + 16
                Text {
                    id: deleteRootLabel
                    anchors.fill: parent; anchors.margins: 8
                    font.family: appearance.codeFont.family; font.pixelSize: Math.max(9, appearance.codeFont.pixelSize - 1)
                    textFormat: Text.PlainText; text: window.deleteProjectRoot
                    color: appearance.colors.muted; wrapMode: Text.WrapAnywhere
                }
            }
            RowLayout {
                Layout.topMargin: 6; Item { Layout.fillWidth: true }
                KButton { objectName: "cancelDeleteProjectButton"; text: qsTr("Annulla"); subtle: true; onClicked: deleteProjectDialog.close() }
                KButton {
                    objectName: "confirmDeleteProjectButton"
                    text: qsTr("Elimina progetto")
                    iconName: "trash"; destructive: true
                    enabled: workspace.connected && window.deleteProjectId.length > 0
                    onClicked: {
                        const pid = window.deleteProjectId
                        deleteProjectDialog.close()
                        workspace.deleteProject(pid)
                    }
                }
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
        property bool rawExpanded: false
        readonly property var parsed: {
            void localization.revision
            return workspace.presentEventDetails(window.eventDetails, window.eventDetailsIsRun)
        }
        onOpened: rawExpanded = false
        ScrollView {
            id: eventDetailsScroll
            anchors.fill: parent
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: eventDetailsScroll.availableWidth
                spacing: 12

                // Status & Duration card
                Rectangle {
                    objectName: "eventDetailsStatusCard"
                    visible: Boolean(eventDialog.parsed && eventDialog.parsed.parsed)
                    Layout.fillWidth: true
                    implicitHeight: statusCol.implicitHeight + 24
                    radius: 10
                    color: appearance.colors.surfaceAlt
                    border.color: appearance.colors.border
                    ColumnLayout {
                        id: statusCol
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 12
                        spacing: 8
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            Rectangle {
                                radius: 6
                                implicitHeight: statusBadgeLabel.implicitHeight + 8
                                implicitWidth: statusBadgeLabel.implicitWidth + 16
                                color: {
                                    const tone = String((eventDialog.parsed && eventDialog.parsed.statusTone) || "muted")
                                    if (tone === "success") return appearance.colors.successBackground
                                    if (tone === "warning") return appearance.colors.warningBackground
                                    if (tone === "danger") return appearance.colors.dangerBackground
                                    if (tone === "accent") return appearance.colors.accentSoft
                                    return appearance.colors.surfaceRaised
                                }
                                border.color: {
                                    const tone = String((eventDialog.parsed && eventDialog.parsed.statusTone) || "muted")
                                    if (tone === "success") return appearance.colors.success
                                    if (tone === "warning") return appearance.colors.warning
                                    if (tone === "danger") return appearance.colors.danger
                                    if (tone === "accent") return appearance.colors.accent
                                    return appearance.colors.border
                                }
                                Text {
                                    id: statusBadgeLabel
                                    objectName: "eventDetailsStatusBadge"
                                    anchors.centerIn: parent
                                    textFormat: Text.PlainText
                                    text: String((eventDialog.parsed && eventDialog.parsed.statusLabel) || "")
                                    font.family: appearance.uiFont.family
                                    font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 1)
                                    font.weight: Font.DemiBold
                                    color: {
                                        const tone = String((eventDialog.parsed && eventDialog.parsed.statusTone) || "muted")
                                        if (tone === "success") return appearance.colors.success
                                        if (tone === "warning") return appearance.colors.warning
                                        if (tone === "danger") return appearance.colors.danger
                                        if (tone === "accent") return appearance.colors.accent
                                        return appearance.colors.textSecondary
                                    }
                                }
                            }
                            Item { Layout.fillWidth: true }
                            Rectangle {
                                visible: Boolean(eventDialog.parsed && eventDialog.parsed.duration)
                                radius: 6
                                implicitHeight: durationBadgeLabel.implicitHeight + 8
                                implicitWidth: durationBadgeLabel.implicitWidth + 14
                                color: appearance.colors.surfaceRaised
                                border.color: appearance.colors.border
                                Text {
                                    id: durationBadgeLabel
                                    objectName: "eventDetailsDurationBadge"
                                    anchors.centerIn: parent
                                    textFormat: Text.PlainText
                                    text: "⏱ " + String((eventDialog.parsed && eventDialog.parsed.duration) || "")
                                    font.family: appearance.codeFont.family
                                    font.pixelSize: Math.max(8, appearance.codeFont.pixelSize - 1)
                                    color: appearance.colors.textSecondary
                                }
                            }
                        }
                        Text {
                            visible: Boolean(eventDialog.parsed && eventDialog.parsed.statusDescription)
                            Layout.fillWidth: true
                            textFormat: Text.PlainText
                            wrapMode: Text.WordWrap
                            text: String((eventDialog.parsed && eventDialog.parsed.statusDescription) || "")
                            font.family: appearance.uiFont.family
                            font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 1)
                            color: appearance.colors.textSecondary
                        }
                    }
                }

                // Error card (if present)
                Rectangle {
                    objectName: "eventDetailsErrorCard"
                    visible: Boolean(eventDialog.parsed && eventDialog.parsed.parsed && eventDialog.parsed.errorText)
                    Layout.fillWidth: true
                    implicitHeight: errorCol.implicitHeight + 24
                    radius: 10
                    color: appearance.colors.dangerBackground
                    border.color: appearance.colors.danger
                    ColumnLayout {
                        id: errorCol
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 12
                        spacing: 6
                        Text {
                            textFormat: Text.PlainText
                            text: qsTr("Errore riscontrato")
                            font.family: appearance.uiFont.family
                            font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 1)
                            font.weight: Font.DemiBold
                            color: appearance.colors.danger
                            Layout.fillWidth: true
                        }
                        ReadText {
                            objectName: "eventDetailsErrorSummary"
                            text: String((eventDialog.parsed && eventDialog.parsed.errorText) || "")
                            Layout.fillWidth: true
                            color: appearance.colors.text
                            font.family: appearance.codeFont.family
                            font.pixelSize: Math.max(8, appearance.codeFont.pixelSize - 1)
                        }
                    }
                }

                // Request / Output card
                Rectangle {
                    objectName: "eventDetailsRequestCard"
                    visible: Boolean(eventDialog.parsed && eventDialog.parsed.parsed && eventDialog.parsed.requestText)
                    Layout.fillWidth: true
                    implicitHeight: reqCol.implicitHeight + 24
                    radius: 10
                    color: appearance.colors.surfaceAlt
                    border.color: appearance.colors.border
                    ColumnLayout {
                        id: reqCol
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 12
                        spacing: 6
                        RowLayout {
                            Layout.fillWidth: true
                            Text {
                                textFormat: Text.PlainText
                                text: (eventDialog.parsed && eventDialog.parsed.isRun) ? qsTr("Richiesta inviata") : qsTr("Contenuto registrato")
                                font.family: appearance.uiFont.family
                                font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 1)
                                font.weight: Font.DemiBold
                                color: appearance.colors.muted
                                Layout.fillWidth: true
                            }
                            KButton {
                                text: qsTr("Copia")
                                subtle: true
                                implicitHeight: 26
                                onClicked: workspace.copy(String((eventDialog.parsed && eventDialog.parsed.requestText) || ""))
                            }
                        }
                        ReadText {
                            objectName: "eventDetailsRequestSummary"
                            text: String((eventDialog.parsed && eventDialog.parsed.requestText) || "")
                            Layout.fillWidth: true
                            color: appearance.colors.text
                        }
                    }
                }

                // Timings card
                Rectangle {
                    objectName: "eventDetailsTimeCard"
                    visible: Boolean(eventDialog.parsed && eventDialog.parsed.parsed && (eventDialog.parsed.timeRows || []).length > 0)
                    Layout.fillWidth: true
                    implicitHeight: timeCol.implicitHeight + 24
                    radius: 10
                    color: appearance.colors.surfaceAlt
                    border.color: appearance.colors.border
                    ColumnLayout {
                        id: timeCol
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 12
                        spacing: 8
                        Text {
                            textFormat: Text.PlainText
                            text: qsTr("Tempi di esecuzione")
                            font.family: appearance.uiFont.family
                            font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 1)
                            font.weight: Font.DemiBold
                            color: appearance.colors.muted
                            Layout.fillWidth: true
                        }
                        Repeater {
                            model: (eventDialog.parsed && eventDialog.parsed.timeRows) ? eventDialog.parsed.timeRows : []
                            delegate: RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 10
                                Text {
                                    textFormat: Text.PlainText
                                    text: String(modelData.label || "")
                                    font.family: appearance.uiFont.family
                                    font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 1)
                                    color: appearance.colors.muted
                                    Layout.fillWidth: true
                                    wrapMode: Text.WordWrap
                                }
                                ReadText {
                                    text: String(modelData.value || "")
                                    font.family: appearance.codeFont.family
                                    font.pixelSize: Math.max(8, appearance.codeFont.pixelSize - 1)
                                    color: appearance.colors.text
                                }
                            }
                        }
                    }
                }

                // Outcome & Diagnostics card
                Rectangle {
                    objectName: "eventDetailsOutcomeCard"
                    visible: Boolean(eventDialog.parsed && eventDialog.parsed.parsed && (eventDialog.parsed.outcomeRows || []).length > 0)
                    Layout.fillWidth: true
                    implicitHeight: outcomeCol.implicitHeight + 24
                    radius: 10
                    color: appearance.colors.surfaceAlt
                    border.color: appearance.colors.border
                    ColumnLayout {
                        id: outcomeCol
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 12
                        spacing: 8
                        Text {
                            textFormat: Text.PlainText
                            text: (eventDialog.parsed && eventDialog.parsed.isRun) ? qsTr("Esito e diagnostica") : qsTr("Parametri dello strumento")
                            font.family: appearance.uiFont.family
                            font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 1)
                            font.weight: Font.DemiBold
                            color: appearance.colors.muted
                            Layout.fillWidth: true
                        }
                        Repeater {
                            model: (eventDialog.parsed && eventDialog.parsed.outcomeRows) ? eventDialog.parsed.outcomeRows : []
                            delegate: RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 10
                                Text {
                                    textFormat: Text.PlainText
                                    text: String(modelData.label || "")
                                    font.family: appearance.uiFont.family
                                    font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 1)
                                    color: appearance.colors.muted
                                    Layout.preferredWidth: Math.min(180, outcomeCol.width * 0.44)
                                    Layout.alignment: Qt.AlignTop
                                    wrapMode: Text.WordWrap
                                }
                                ReadText {
                                    text: String(modelData.value || "")
                                    Layout.fillWidth: true
                                    Layout.alignment: Qt.AlignTop
                                    color: appearance.colors.text
                                    font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 1)
                                }
                            }
                        }
                    }
                }

                // Notes card (if any trailing notes exist)
                Rectangle {
                    visible: Boolean(eventDialog.parsed && eventDialog.parsed.parsed && eventDialog.parsed.notesText)
                    Layout.fillWidth: true
                    implicitHeight: notesCol.implicitHeight + 20
                    radius: 10
                    color: appearance.colors.surfaceAlt
                    border.color: appearance.colors.border
                    ColumnLayout {
                        id: notesCol
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 10
                        ReadText {
                            text: String((eventDialog.parsed && eventDialog.parsed.notesText) || "")
                            Layout.fillWidth: true
                            color: appearance.colors.textSecondary
                            font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1)
                        }
                    }
                }

                // Technical References card
                Rectangle {
                    objectName: "eventDetailsRefCard"
                    visible: Boolean(eventDialog.parsed && eventDialog.parsed.parsed && (eventDialog.parsed.referenceRows || []).length > 0)
                    Layout.fillWidth: true
                    implicitHeight: refCol.implicitHeight + 24
                    radius: 10
                    color: appearance.colors.surfaceAlt
                    border.color: appearance.colors.border
                    ColumnLayout {
                        id: refCol
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 12
                        spacing: 8
                        Text {
                            textFormat: Text.PlainText
                            text: qsTr("Riferimenti tecnici")
                            font.family: appearance.uiFont.family
                            font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 1)
                            font.weight: Font.DemiBold
                            color: appearance.colors.muted
                            Layout.fillWidth: true
                        }
                        Repeater {
                            model: (eventDialog.parsed && eventDialog.parsed.referenceRows) ? eventDialog.parsed.referenceRows : []
                            delegate: RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 10
                                Text {
                                    textFormat: Text.PlainText
                                    text: String(modelData.label || "")
                                    font.family: appearance.uiFont.family
                                    font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2)
                                    color: appearance.colors.muted
                                    Layout.preferredWidth: Math.min(130, refCol.width * 0.36)
                                    Layout.alignment: Qt.AlignTop
                                    wrapMode: Text.WordWrap
                                }
                                ReadText {
                                    text: String(modelData.value || "")
                                    Layout.fillWidth: true
                                    Layout.alignment: Qt.AlignTop
                                    font.family: appearance.codeFont.family
                                    font.pixelSize: Math.max(8, appearance.codeFont.pixelSize - 1)
                                    color: appearance.colors.textSecondary
                                }
                            }
                        }
                    }
                }

                // Raw JSON toggle & copy actions
                RowLayout {
                    visible: Boolean(eventDialog.parsed && eventDialog.parsed.parsed)
                    Layout.fillWidth: true
                    spacing: 8
                    KButton {
                        objectName: "eventDetailsToggleRaw"
                        subtle: true
                        text: eventDialog.rawExpanded ? qsTr("Nascondi dati grezzi (JSON)") : qsTr("Mostra dati grezzi (JSON)")
                        onClicked: eventDialog.rawExpanded = !eventDialog.rawExpanded
                    }
                    Item { Layout.fillWidth: true }
                    KButton {
                        objectName: "eventDetailsCopyRaw"
                        subtle: true
                        text: qsTr("Copia JSON")
                        onClicked: workspace.copy(window.eventDetails)
                    }
                }

                // Raw JSON container (always present in visual tree for literal inspection)
                Rectangle {
                    visible: !Boolean(eventDialog.parsed && eventDialog.parsed.parsed) || eventDialog.rawExpanded
                    Layout.fillWidth: true
                    implicitHeight: rawTextCol.implicitHeight + 20
                    radius: 10
                    color: appearance.colors.codeBackground
                    border.color: appearance.colors.border
                    ColumnLayout {
                        id: rawTextCol
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 10
                        ReadText {
                            id: rawEventDetailsText
                            objectName: "eventDetailsText"
                            color: appearance.colors.codeText
                            text: window.eventDetails
                            Layout.fillWidth: true
                            font.family: appearance.codeFont.family
                            font.styleName: appearance.codeFont.styleName
                            font.weight: appearance.codeFont.weight
                            font.italic: appearance.codeFont.italic
                            font.pixelSize: appearance.codeFont.pixelSize
                        }
                    }
                }
            }
        }
    }
    Dialog {
        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
        id: runErrorReportDialog
        objectName: "runErrorReportDialog"
        title: qsTr("Conferma invio segnalazione errore")
        modal: true
        anchors.centerIn: parent
        width: Math.min(720, window.width - 50)
        height: Math.min(580, window.height - 60)
        background: Rectangle { color: appearance.colors.surface; radius: 12; border.color: appearance.colors.border }
        ColumnLayout {
            anchors.fill: parent
            spacing: 12
            Text {
                font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic
                textFormat: Text.PlainText
                text: qsTr("Verifica l’esatto testo che stai per inviare. Il testo della tua richiesta, i file del progetto e le credenziali personali sono esclusi.")
                color: appearance.colors.muted
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1)
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: 10
                color: appearance.colors.codeBackground
                border.color: appearance.colors.border
                ScrollView {
                    id: runErrorReportScroll
                    anchors.fill: parent
                    anchors.margins: 10
                    clip: true
                    contentWidth: availableWidth
                    ReadText {
                        id: runErrorReportPreviewText
                        objectName: "runErrorReportPreviewText"
                        color: appearance.colors.codeText
                        text: workspace.runErrorReportPreview
                        width: runErrorReportScroll.availableWidth
                        font.family: appearance.codeFont.family
                        font.styleName: appearance.codeFont.styleName
                        font.weight: appearance.codeFont.weight
                        font.italic: appearance.codeFont.italic
                        font.pixelSize: appearance.codeFont.pixelSize
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                KButton {
                    text: qsTr("Copia")
                    subtle: true
                    enabled: workspace.runErrorReportPreview.length > 0
                    onClicked: workspace.copy(workspace.runErrorReportPreview)
                }
                Item { Layout.fillWidth: true }
                KButton {
                    objectName: "cancelRunErrorReportButton"
                    text: qsTr("Annulla")
                    onClicked: runErrorReportDialog.close()
                }
                KButton {
                    objectName: "confirmRunErrorReportButton"
                    text: qsTr("Conferma e invia")
                    primary: true
                    enabled: !workspace.diagnosticsSending && !Boolean(workspace.runFailure.alreadyReported) && workspace.runErrorReportPreview.length > 0
                    onClicked: {
                        workspace.sendRunErrorReport()
                        runErrorReportDialog.close()
                    }
                }
            }
        }
    }
    KiraPanel {
        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
        id: artifactsDialog; objectName: "artifactsDialog"; title: qsTr("Artefatti del progetto"); inspectorKey: "file"
        standardButtons: Dialog.Close
        ColumnLayout {
            anchors.fill: parent
            spacing: 10
            KButton {
                objectName: "artifactsOpenKiraExplorerButton"
                Layout.fillWidth: true
                text: qsTr("Esplora tutti i file nell'editor di Kira")
                primary: true
                onClicked: {
                    artifactsDialog.close()
                    workspace.canvas.openProjectExplorer()
                }
            }
            ListView {
                id: artifactList; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 8; model: workspace.artifacts
                ScrollBar.vertical: ScrollBar {}
                delegate: RowLayout {
                    required property var modelData
                    width: artifactList.width
                    spacing: 6
                    KButton {
                        Layout.fillWidth: true
                        text: "↗  " + (modelData.rel_path || String(modelData.path).split("/").pop())
                        onClicked: workspace.openArtifact(modelData.path)
                        toolTipText: modelData.path
                    }
                    KButton {
                        objectName: "openArtifactInKira_" + String(modelData.path).split("/").pop()
                        text: qsTr("Editor Kira")
                        subtle: true
                        onClicked: {
                            artifactsDialog.close()
                            workspace.openInKiraEditor(modelData.path)
                        }
                        toolTipText: qsTr("Apri nell'editor integrato di Kira")
                    }
                }
                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; textFormat: Text.PlainText; text: qsTr("Nessun artefatto registrato."); width: artifactList.width; wrapMode: Text.Wrap; visible: artifactList.count === 0; color: muted }
            }
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
    AmbientOverlay {}
}
