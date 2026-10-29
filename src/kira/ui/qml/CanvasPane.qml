import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: pane
    objectName: "canvasPane"
    color: appearance.colors.surface
    radius: 16
    border.color: appearance.colors.border

    property int cursorLine: 1
    property int cursorCol: 1
    property bool showCanvasTools: false

    function updateCursorAndSelection() {
        var txt = editorArea.text || "";
        var pos = Math.max(0, Math.min(editorArea.cursorPosition, txt.length));
        var before = txt.substring(0, pos);
        var linesBefore = before.split("\n");
        pane.cursorLine = linesBefore.length;
        pane.cursorCol = linesBefore[linesBefore.length - 1].length + 1;

        if (editorArea.selectedText && editorArea.selectedText.length > 0) {
            var sStart = Math.max(0, Math.min(editorArea.selectionStart, txt.length));
            var sEnd = Math.max(0, Math.min(editorArea.selectionEnd, txt.length));
            var startLine = txt.substring(0, sStart).split("\n").length;
            var endLine = txt.substring(0, sEnd).split("\n").length;
            if (!selStartField.activeFocus) {
                selStartField.text = String(startLine);
            }
            if (!selEndField.activeFocus) {
                selEndField.text = String(Math.max(startLine, endLine));
            }
        }
    }

    Connections {
        target: canvas
        function onChanged() {
            if (!editorArea.activeFocus || editorArea.text !== canvas.currentContent) {
                if (!canvas.activeFileDirty) {
                    editorArea.text = canvas.currentContent
                }
            }
            if (!titleField.activeFocus) {
                titleField.text = canvas.currentTitle
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 6

        // 1. Compact Professional Top Header Bar
        RowLayout {
            Layout.fillWidth: true
            spacing: 6

            Rectangle {
                Layout.preferredWidth: 26
                Layout.preferredHeight: 26
                radius: 7
                color: canvas.isProjectFileActive ? appearance.colors.accentSoft : appearance.colors.surfaceAlt
                border.color: canvas.isProjectFileActive ? appearance.colors.accent : appearance.colors.border
                Text {
                    anchors.centerIn: parent
                    text: canvas.isProjectFileActive ? "{}" : "◈"
                    font.family: appearance.codeFont.family
                    font.pixelSize: 12
                    font.weight: Font.Bold
                    color: canvas.isProjectFileActive ? appearance.colors.accent : appearance.colors.muted
                    textFormat: Text.PlainText
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0
                Text {
                    objectName: "canvasPaneTitle"
                    text: canvas.currentId.length > 0
                        ? (canvas.isProjectFileActive ? (canvas.activeFileDirty ? "● " + canvas.currentTitle : canvas.currentTitle) : canvas.currentTitle)
                        : qsTr("Editor & Canvas Interattivo di Kira")
                    font.family: appearance.uiFont.family
                    font.pixelSize: appearance.uiFont.pixelSize + 1
                    font.weight: Font.DemiBold
                    color: appearance.colors.text
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                    textFormat: Text.PlainText
                }
                Text {
                    text: canvas.currentId.length > 0
                        ? (canvas.isProjectFileActive
                            ? qsTr("File del progetto · %1 · %2 righe").arg(canvas.currentLanguage).arg(canvas.currentLineCount)
                            : qsTr("Tipo: %1 · Rev. %2 · %3 righe").arg(canvas.currentKind).arg(canvas.currentRevision).arg(canvas.currentLineCount))
                        : qsTr("Editor multi-tab per i file del progetto, anteprima live Mermaid/HTML/SVG e diff granulare")
                    font.family: appearance.uiFont.family
                    font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 3)
                    color: appearance.colors.muted
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                    textFormat: Text.PlainText
                }
            }

            KButton {
                objectName: "canvasSaveRevisionButton"
                visible: canvas.currentId.length > 0
                text: canvas.isProjectFileActive ? qsTr("Salva file (Ctrl+S)") : qsTr("Salva revisione")
                primary: canvas.activeFileDirty || !canvas.isProjectFileActive
                subtle: canvas.isProjectFileActive && !canvas.activeFileDirty
                enabled: canvas.currentId.length > 0 && !canvas.busy
                onClicked: canvas.saveContent(editorArea.text, titleField.text)
            }
            KButton {
                objectName: "canvasFileExplorerToggle"
                text: qsTr("File del progetto")
                checked: canvas.fileExplorerVisible
                subtle: !canvas.fileExplorerVisible
                onClicked: canvas.toggleFileExplorer()
            }
            KButton {
                objectName: "canvasRefreshButton"
                text: qsTr("Aggiorna")
                subtle: true
                enabled: (workspace.connected || canvas.isProjectFileActive) && !canvas.busy
                onClicked: canvas.refresh()
            }
            KButton {
                objectName: "canvasExpandButton"
                text: canvas.expanded ? qsTr("Riduci") : qsTr("Espandi")
                subtle: true
                onClicked: canvas.toggleExpanded()
            }
            KButton {
                objectName: "canvasCloseButton"
                text: qsTr("Chiudi")
                subtle: true
                onClicked: canvas.hideCanvas()
            }
        }

        // 2. Unified IDE Tab Bar + View Mode Switcher
        Rectangle {
            visible: canvas.openFileTabs.length > 0 || canvas.currentId.length > 0
            Layout.fillWidth: true
            Layout.preferredHeight: 36
            color: appearance.colors.surfaceAlt
            radius: 9
            border.color: appearance.colors.border

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 4
                anchors.rightMargin: 4
                spacing: 6

                ScrollView {
                    id: openFileTabsBar
                    objectName: "canvasOpenFileTabsBar"
                    visible: canvas.openFileTabs.length > 0
                    Layout.fillWidth: true
                    Layout.preferredHeight: 30
                    clip: true
                    ScrollBar.horizontal.policy: ScrollBar.AsNeeded
                    RowLayout {
                        spacing: 4
                        Repeater {
                            model: canvas.openFileTabs
                            delegate: Rectangle {
                                required property var modelData
                                objectName: "canvasFileTab_" + modelData.rel_path
                                readonly property bool isCurrent: canvas.activeFilePath === modelData.path
                                implicitHeight: 28
                                implicitWidth: tabRow.implicitWidth + 14
                                radius: 6
                                color: isCurrent ? appearance.colors.surface : "transparent"
                                border.width: isCurrent ? 1 : 0
                                border.color: isCurrent ? appearance.colors.accent : "transparent"

                                Rectangle {
                                    visible: isCurrent
                                    anchors.top: parent.top
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.leftMargin: 4
                                    anchors.rightMargin: 4
                                    height: 2
                                    radius: 1
                                    color: appearance.colors.accent
                                }

                                RowLayout {
                                    id: tabRow
                                    anchors.centerIn: parent
                                    spacing: 5
                                    MouseArea {
                                        Layout.preferredWidth: tabLabel.implicitWidth
                                        Layout.preferredHeight: 24
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            canvas.selectFileTab(modelData.path)
                                            editorArea.text = canvas.currentContent
                                            titleField.text = canvas.currentTitle
                                        }
                                        Text {
                                            id: tabLabel
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: (modelData.dirty ? "● " : "") + modelData.name
                                            font.family: appearance.codeFont.family
                                            font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 1)
                                            font.weight: isCurrent ? Font.DemiBold : Font.Normal
                                            color: isCurrent ? appearance.colors.text : appearance.colors.muted
                                            textFormat: Text.PlainText
                                        }
                                    }
                                    KButton {
                                        objectName: "canvasCloseFileTab_" + modelData.rel_path
                                        text: "×"
                                        subtle: true
                                        implicitWidth: 20
                                        implicitHeight: 20
                                        onClicked: canvas.closeFileTab(modelData.path)
                                    }
                                }
                            }
                        }
                    }
                }

                Item {
                    visible: canvas.openFileTabs.length === 0
                    Layout.fillWidth: true
                }

                RowLayout {
                    visible: canvas.currentId.length > 0
                    spacing: 4
                    KButton {
                        objectName: "canvasTabCode"
                        text: qsTr("Editor & Selezione Righe")
                        checked: canvas.viewMode === "code"
                        subtle: canvas.viewMode !== "code"
                        onClicked: {
                            editorArea.text = canvas.currentContent
                            titleField.text = canvas.currentTitle
                            canvas.setViewMode("code")
                        }
                    }
                    KButton {
                        objectName: "canvasTabPreview"
                        text: qsTr("Anteprima Live")
                        checked: canvas.viewMode === "preview"
                        subtle: canvas.viewMode !== "preview"
                        onClicked: canvas.setViewMode("preview")
                    }
                    KButton {
                        objectName: "canvasTabDiff"
                        visible: !canvas.isProjectFileActive || canvas.diffHunks.length > 0
                        text: qsTr("Diff Blocchi (%1)").arg(canvas.diffHunks.length)
                        checked: canvas.viewMode === "diff"
                        subtle: canvas.viewMode !== "diff"
                        onClicked: canvas.setViewMode("diff")
                    }
                    KButton {
                        objectName: "canvasTabHistory"
                        visible: !canvas.isProjectFileActive
                        text: qsTr("Revisioni (%1)").arg(canvas.history.length)
                        checked: canvas.viewMode === "history"
                        subtle: canvas.viewMode !== "history"
                        onClicked: canvas.setViewMode("history")
                    }
                    KButton {
                        objectName: "canvasDeleteButton"
                        text: canvas.isProjectFileActive ? qsTr("Chiudi scheda") : qsTr("Elimina")
                        subtle: true
                        enabled: canvas.currentId.length > 0 && !canvas.busy
                        onClicked: canvas.deleteArtifact(canvas.currentId)
                    }
                }
            }
        }

        // 3. Canvas Preset Strip & Artifacts (automatically hidden when editing a project file to maximize space)
        Flow {
            visible: !canvas.isProjectFileActive || pane.showCanvasTools
            Layout.fillWidth: true
            spacing: 6
            KButton {
                objectName: "canvasNewMermaidButton"
                text: qsTr("+ Mermaid")
                subtle: true
                enabled: workspace.connected && !canvas.busy
                onClicked: canvas.createPreset("mermaid")
            }
            KButton {
                objectName: "canvasNewHtmlButton"
                text: qsTr("+ HTML / UI")
                subtle: true
                enabled: workspace.connected && !canvas.busy
                onClicked: canvas.createPreset("html")
            }
            KButton {
                objectName: "canvasNewSvgButton"
                text: qsTr("+ SVG")
                subtle: true
                enabled: workspace.connected && !canvas.busy
                onClicked: canvas.createPreset("svg")
            }
            KButton {
                objectName: "canvasNewMarkdownButton"
                text: qsTr("+ Documento")
                subtle: true
                enabled: workspace.connected && !canvas.busy
                onClicked: canvas.createPreset("markdown")
            }
            KButton {
                objectName: "canvasNewCodeButton"
                text: qsTr("+ Codice")
                subtle: true
                enabled: workspace.connected && !canvas.busy
                onClicked: canvas.createPreset("code")
            }
        }

        ScrollView {
            visible: (!canvas.isProjectFileActive || pane.showCanvasTools) && canvas.artifacts.length > 0
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? 34 : 0
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AsNeeded
            RowLayout {
                spacing: 6
                Repeater {
                    model: canvas.artifacts
                    delegate: KButton {
                        required property var modelData
                        objectName: "canvasArtifactChip_" + modelData.id
                        text: (modelData.title || modelData.id) + " (v" + (modelData.revision || 1) + ")"
                        checked: !canvas.isProjectFileActive && canvas.currentId === modelData.id
                        subtle: canvas.isProjectFileActive || canvas.currentId !== modelData.id
                        onClicked: canvas.selectArtifact(modelData.id)
                    }
                }
            }
        }

        Text {
            objectName: "canvasErrorLabel"
            visible: canvas.error.length > 0
            text: canvas.error
            color: appearance.colors.warning
            font: appearance.uiFont
            wrapMode: Text.Wrap
            Layout.fillWidth: true
            textFormat: Text.PlainText
        }

        Text {
            objectName: "canvasStatusLabel"
            visible: canvas.statusText.length > 0 && !canvas.isProjectFileActive
            text: canvas.statusText
            color: appearance.colors.accent
            font: appearance.uiFont
            wrapMode: Text.Wrap
            Layout.fillWidth: true
            textFormat: Text.PlainText
        }

        // 4. Main IDE Workspace: Left Sidebar Explorer + Right Editor/Preview Area
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 8

            // Left Sidebar: Project File Explorer (VS Code style vertical tree instead of taking top vertical space)
            Rectangle {
                id: fileExplorerPanel
                objectName: "canvasFileExplorerPanel"
                visible: canvas.fileExplorerVisible
                Layout.preferredWidth: visible ? 215 : 0
                Layout.fillHeight: true
                color: appearance.colors.surfaceAlt
                radius: 10
                border.color: appearance.colors.border
                clip: true

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 6

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        Text {
                            text: qsTr("File del progetto")
                            font.family: appearance.uiFont.family
                            font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 2)
                            font.weight: Font.DemiBold
                            color: appearance.colors.muted
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                        }
                        Text {
                            text: qsTr("%1 file").arg(canvas.projectFiles.length)
                            font.family: appearance.codeFont.family
                            font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 3)
                            color: appearance.colors.muted
                            textFormat: Text.PlainText
                        }
                    }

                    TextField {
                        id: fileSearchField
                        objectName: "canvasFileSearchField"
                        Layout.fillWidth: true
                        implicitHeight: 30
                        font: appearance.uiFont
                        selectByMouse: true
                        text: canvas.fileSearchQuery
                        placeholderText: qsTr("Cerca file nel progetto per nome o percorso…")
                        onTextEdited: canvas.searchProjectFiles(text)
                    }

                    ListView {
                        id: projectFileList
                        objectName: "canvasProjectFileList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 2
                        model: canvas.projectFiles
                        ScrollBar.vertical: KScrollBar {}
                        delegate: KButton {
                            required property var modelData
                            objectName: "canvasProjectFileItem_" + modelData.rel_path
                            width: projectFileList.width
                            implicitHeight: 26
                            leftAligned: true
                            checked: canvas.activeFilePath === modelData.path
                            subtle: canvas.activeFilePath !== modelData.path
                            text: modelData.rel_path
                            onClicked: {
                                canvas.openProjectFile(modelData.path)
                                editorArea.text = canvas.currentContent
                                titleField.text = canvas.currentTitle
                            }
                        }
                    }
                }
            }

            // Right Main Stack: Empty State / Live Preview / Professional Code Editor / Diff / History
            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: canvas.currentId.length === 0 ? 0
                    : canvas.viewMode === "code" ? 2
                    : canvas.viewMode === "diff" ? 3
                    : canvas.viewMode === "history" ? 4 : 1

                // Index 0: Empty State
                Rectangle {
                    color: appearance.colors.surfaceAlt
                    radius: 12
                    border.color: appearance.colors.border
                    ColumnLayout {
                        anchors.centerIn: parent
                        width: Math.min(parent.width - 32, 420)
                        spacing: 10
                        Text {
                            text: qsTr("Nessun file o artefatto attivo nell'editor.")
                            font.family: appearance.uiFont.family
                            font.pixelSize: appearance.uiFont.pixelSize + 2
                            font.weight: Font.DemiBold
                            color: appearance.colors.text
                            horizontalAlignment: Text.AlignHCenter
                            Layout.fillWidth: true
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                        }
                        Text {
                            text: qsTr("Premi «File del progetto» per aprire qualsiasi file nell'editor integrato di Kira, oppure crea un diagramma Mermaid, un mockup HTML/Tailwind o un documento nel Canvas.")
                            font: appearance.uiFont
                            color: appearance.colors.muted
                            horizontalAlignment: Text.AlignHCenter
                            Layout.fillWidth: true
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                        }
                        KButton {
                            Layout.alignment: Qt.AlignHCenter
                            text: qsTr("Esplora i file del progetto")
                            primary: true
                            onClicked: canvas.openProjectExplorer()
                        }
                    }
                }

                // Index 1: Live Preview
                Rectangle {
                    color: appearance.colors.surfaceAlt
                    radius: 12
                    border.color: appearance.colors.border
                    clip: true
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 4
                        spacing: 6
                        Rectangle {
                            id: previewViewport
                            objectName: "canvasPreviewViewport"
                            Layout.fillWidth: true
                            Layout.fillHeight: canvas.currentKind !== "markdown" && canvas.currentKind !== "code"
                            Layout.preferredHeight: (canvas.currentKind === "markdown" || canvas.currentKind === "code") ? 240 : -1
                            color: "transparent"
                            radius: 8
                            clip: true
                            onWidthChanged: canvas.setViewport(Math.floor(width), Math.floor(height))
                            onHeightChanged: canvas.setViewport(Math.floor(width), Math.floor(height))
                            Component.onCompleted: canvas.setViewport(Math.floor(width), Math.floor(height))

                            Image {
                                id: livePreviewImage
                                objectName: "canvasLivePreviewImage"
                                anchors.fill: parent
                                source: canvas.liveFrameUrl.length > 0 ? canvas.liveFrameUrl : canvas.previewUrl
                                cache: false
                                asynchronous: false
                                mipmap: true
                                smooth: true
                                fillMode: canvas.liveFrameUrl.length > 0 ? Image.Stretch : Image.PreserveAspectFit
                                horizontalAlignment: Image.AlignHCenter
                                verticalAlignment: Image.AlignVCenter
                            }

                            MouseArea {
                                id: canvasPointerArea
                                objectName: "canvasPointerArea"
                                anchors.fill: parent
                                enabled: canvas.currentKind === "html"
                                hoverEnabled: true
                                acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
                                preventStealing: true
                                onPositionChanged: function(mouse) {
                                    canvas.sendPointer("mouseMoved", mouse.x, mouse.y, mouse.button, mouse.buttons, 0, 0)
                                }
                                onPressed: function(mouse) {
                                    canvas.sendPointer("mousePressed", mouse.x, mouse.y, mouse.button, mouse.buttons, 0, 0)
                                }
                                onReleased: function(mouse) {
                                    canvas.sendPointer("mouseReleased", mouse.x, mouse.y, mouse.button, mouse.buttons, 0, 0)
                                }
                                onWheel: function(wheel) {
                                    canvas.sendPointer("mouseWheel", wheel.x, wheel.y, 0, wheel.buttons, -wheel.angleDelta.x, -wheel.angleDelta.y)
                                    wheel.accepted = true
                                }
                            }
                        }
                        ScrollView {
                            id: docPreviewScroll
                            visible: canvas.currentKind === "markdown" || canvas.currentKind === "code"
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            ReadText {
                                width: docPreviewScroll.availableWidth
                                text: canvas.currentContent
                                font: canvas.currentKind === "code" ? appearance.codeFont : appearance.uiFont
                                color: appearance.colors.text
                                wrapMode: TextEdit.Wrap
                            }
                        }
                    }
                }

                // Index 2: Professional Code Editor + Compact AI Inline Bar + IDE Status Bar
                ColumnLayout {
                    spacing: 6

                    TextField {
                        id: titleField
                        objectName: "canvasTitleField"
                        visible: !canvas.isProjectFileActive
                        Layout.fillWidth: true
                        implicitHeight: visible ? 32 : 0
                        font: appearance.uiFont
                        readOnly: canvas.isProjectFileActive
                        selectByMouse: true
                        text: canvas.currentTitle
                        placeholderText: qsTr("Titolo dell'artefatto…")
                    }

                    // Main Code Surface
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        color: appearance.dark ? "#0d1117" : "#f8fafc"
                        radius: 10
                        border.color: editorArea.activeFocus ? appearance.colors.accent : appearance.colors.border
                        clip: true

                        Flickable {
                            id: codeEditorFlick
                            anchors.fill: parent
                            contentWidth: Math.max(width, gutterCol.width + editorArea.implicitWidth + 24)
                            contentHeight: Math.max(height, editorArea.contentHeight + 24)
                            clip: true
                            boundsBehavior: Flickable.StopAtBounds
                            ScrollBar.vertical: KScrollBar {}
                            ScrollBar.horizontal: KScrollBar {}

                            // Active cursor line subtle highlight
                            Rectangle {
                                x: gutterCol.width
                                y: editorArea.cursorRectangle.y
                                width: Math.max(codeEditorFlick.width, codeEditorFlick.contentWidth) - gutterCol.width
                                height: Math.max(18, editorArea.cursorRectangle.height)
                                color: appearance.dark ? "#161b22" : "#eef2ff"
                                visible: editorArea.activeFocus
                            }

                            // Pixel-Aligned Line Numbers Gutter (inside same Flickable so scrolling never desynchronizes)
                            Rectangle {
                                id: gutterCol
                                objectName: "canvasLineNumbersGutter"
                                x: codeEditorFlick.contentX
                                y: 0
                                z: 2
                                width: 48
                                height: Math.max(codeEditorFlick.height, codeEditorFlick.contentHeight)
                                color: appearance.dark ? "#090d13" : "#f1f5f9"

                                Rectangle {
                                    anchors.right: parent.right
                                    anchors.top: parent.top
                                    anchors.bottom: parent.bottom
                                    width: 1
                                    color: appearance.colors.border
                                }

                                Text {
                                    anchors.top: parent.top
                                    anchors.topMargin: editorArea.topPadding
                                    anchors.right: parent.right
                                    anchors.rightMargin: 10
                                    font: appearance.codeFont
                                    color: appearance.colors.muted
                                    horizontalAlignment: Text.AlignRight
                                    textFormat: Text.PlainText
                                    text: {
                                        var count = Math.max(1, editorArea.lineCount || canvas.currentLineCount);
                                        var out = [];
                                        for (var i = 1; i <= Math.min(count, 4000); ++i) {
                                            out.push(i);
                                        }
                                        return out.join("\n");
                                    }
                                }
                            }

                            TextArea {
                                id: editorArea
                                objectName: "canvasCodeEditor"
                                x: gutterCol.width
                                y: 0
                                width: Math.max(codeEditorFlick.width - gutterCol.width, implicitWidth + 32)
                                height: Math.max(codeEditorFlick.height, contentHeight + 24)
                                topPadding: 12
                                bottomPadding: 12
                                leftPadding: 12
                                rightPadding: 16
                                font: appearance.codeFont
                                color: appearance.dark ? "#e6edf3" : "#0f172a"
                                selectionColor: appearance.dark ? "#264f78" : "#bfdbfe"
                                selectedTextColor: appearance.dark ? "#ffffff" : "#0f172a"
                                text: canvas.currentContent
                                wrapMode: TextEdit.NoWrap
                                selectByMouse: true
                                textFormat: TextEdit.PlainText
                                background: Item {}

                                Component.onCompleted: {
                                    canvas.attachHighlighter(editorArea.textDocument)
                                }

                                onCursorPositionChanged: {
                                    pane.updateCursorAndSelection()
                                }
                                onSelectedTextChanged: {
                                    pane.updateCursorAndSelection()
                                }
                                onCursorRectangleChanged: {
                                    if (cursorRectangle.y < codeEditorFlick.contentY) {
                                        codeEditorFlick.contentY = Math.max(0, cursorRectangle.y - 12)
                                    } else if (cursorRectangle.y + cursorRectangle.height > codeEditorFlick.contentY + codeEditorFlick.height - 12) {
                                        codeEditorFlick.contentY = cursorRectangle.y + cursorRectangle.height - codeEditorFlick.height + 12
                                    }
                                }
                                onTextChanged: {
                                    if (canvas.isProjectFileActive) {
                                        canvas.updateEditorDraft(text)
                                    }
                                }
                                Keys.onPressed: function(event) {
                                    if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_S) {
                                        canvas.saveContent(editorArea.text, titleField.text)
                                        event.accepted = true
                                    } else if (event.key === Qt.Key_Tab) {
                                        editorArea.insert(editorArea.cursorPosition, "    ")
                                        event.accepted = true
                                    }
                                }
                            }
                        }
                    }

                    // Sleek 1-Line AI Collaborative Selection Toolbar
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: 40
                        color: appearance.colors.surfaceAlt
                        radius: 9
                        border.color: appearance.colors.border

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            anchors.rightMargin: 8
                            spacing: 6

                            Text {
                                text: qsTr("Da riga:")
                                font.family: appearance.uiFont.family
                                font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 2)
                                color: appearance.colors.muted
                                textFormat: Text.PlainText
                            }
                            TextField {
                                id: selStartField
                                objectName: "canvasSelStartField"
                                Layout.preferredWidth: 44
                                implicitHeight: 28
                                font: appearance.codeFont
                                text: "1"
                                selectByMouse: true
                            }
                            Text {
                                text: qsTr("A riga:")
                                font.family: appearance.uiFont.family
                                font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 2)
                                color: appearance.colors.muted
                                textFormat: Text.PlainText
                            }
                            TextField {
                                id: selEndField
                                objectName: "canvasSelEndField"
                                Layout.preferredWidth: 44
                                implicitHeight: 28
                                font: appearance.codeFont
                                text: String(Math.max(1, canvas.currentLineCount))
                                selectByMouse: true
                            }
                            TextField {
                                id: selPromptField
                                objectName: "canvasSelPromptField"
                                Layout.fillWidth: true
                                implicitHeight: 28
                                font: appearance.uiFont
                                selectByMouse: true
                                placeholderText: qsTr("Es. Ottimizza queste righe o spiega questa funzione…")
                                onAccepted: {
                                    if (canvas.currentId.length > 0) {
                                        canvas.askKiraSelection(
                                            parseInt(selStartField.text || "1"),
                                            parseInt(selEndField.text || "1"),
                                            selPromptField.text,
                                            editorArea.selectedText
                                        )
                                    }
                                }
                            }
                            KButton {
                                objectName: "canvasAttachSelectionButton"
                                text: qsTr("Allega alla chat")
                                subtle: true
                                enabled: canvas.currentId.length > 0
                                onClicked: canvas.attachSelectionToChat(
                                    parseInt(selStartField.text || "1"),
                                    parseInt(selEndField.text || "1"),
                                    editorArea.selectedText
                                )
                            }
                            KButton {
                                objectName: "canvasAskSelectionButton"
                                text: qsTr("Spiega / Chiedi")
                                subtle: true
                                enabled: canvas.currentId.length > 0
                                onClicked: canvas.askKiraSelection(
                                    parseInt(selStartField.text || "1"),
                                    parseInt(selEndField.text || "1"),
                                    selPromptField.text,
                                    editorArea.selectedText
                                )
                            }
                            KButton {
                                objectName: "canvasOptimizeSelectionButton"
                                text: qsTr("Correggi / Ottimizza")
                                primary: true
                                enabled: canvas.currentId.length > 0
                                onClicked: canvas.optimizeSelection(
                                    parseInt(selStartField.text || "1"),
                                    parseInt(selEndField.text || "1"),
                                    selPromptField.text,
                                    editorArea.selectedText
                                )
                            }
                        }
                    }

                    // Professional IDE Status Bar (VS Code / Cursor style)
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: 22
                        color: appearance.dark ? "#090d13" : appearance.colors.surfaceAlt
                        radius: 6
                        border.color: appearance.colors.border

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            anchors.rightMargin: 8
                            spacing: 12

                            Text {
                                text: canvas.activeFileDirty ? "● " + qsTr("Da salvare") : "✓ " + (canvas.statusText.length > 0 ? canvas.statusText : "UTF-8")
                                font.family: appearance.codeFont.family
                                font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 3)
                                color: canvas.activeFileDirty ? appearance.colors.warning : appearance.colors.accent
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                                textFormat: Text.PlainText
                            }

                            Text {
                                text: "Ln " + pane.cursorLine + ", Col " + pane.cursorCol
                                font.family: appearance.codeFont.family
                                font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 3)
                                color: appearance.colors.muted
                                textFormat: Text.PlainText
                            }

                            Text {
                                text: (canvas.currentLanguage || canvas.currentKind || "text").toUpperCase()
                                font.family: appearance.codeFont.family
                                font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 3)
                                font.weight: Font.DemiBold
                                color: appearance.colors.text
                                textFormat: Text.PlainText
                            }
                        }
                    }
                }

                // Index 3: Diff Hunks View
                ColumnLayout {
                    spacing: 8
                    Text {
                        text: qsTr("Revisione granulare dei blocchi modificati rispetto alla revisione precedente. Puoi accettare o rifiutare ogni singolo blocco.")
                        font: appearance.uiFont
                        color: appearance.colors.muted
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                        textFormat: Text.PlainText
                    }
                    ListView {
                        id: canvasHunkList
                        objectName: "canvasHunkList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 8
                        model: canvas.diffHunks
                        ScrollBar.vertical: KScrollBar {}
                        delegate: Rectangle {
                            required property var modelData
                            width: canvasHunkList.width
                            implicitHeight: hunkCol.implicitHeight + 18
                            color: appearance.colors.surfaceAlt
                            radius: 10
                            border.color: appearance.colors.border
                            ColumnLayout {
                                id: hunkCol
                                anchors.fill: parent
                                anchors.margins: 9
                                spacing: 6
                                RowLayout {
                                    Layout.fillWidth: true
                                    Text {
                                        text: qsTr("Blocco #%1 · %2 (+%3 / -%4)").arg(Number(modelData.index || 0) + 1).arg(modelData.header || "").arg(modelData.added_count || 0).arg(modelData.removed_count || 0)
                                        font: appearance.codeFont
                                        color: appearance.colors.accent
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }
                                    KButton {
                                        objectName: "canvasAcceptHunk_" + modelData.index
                                        text: qsTr("Accetta blocco")
                                        subtle: true
                                        enabled: !canvas.busy
                                        onClicked: canvas.applyHunk(modelData.index, "accept")
                                    }
                                    KButton {
                                        objectName: "canvasRejectHunk_" + modelData.index
                                        text: qsTr("Rifiuta blocco")
                                        enabled: !canvas.busy
                                        onClicked: canvas.applyHunk(modelData.index, "reject")
                                    }
                                }
                                ReadText {
                                    Layout.fillWidth: true
                                    text: (modelData.lines || []).join("\n")
                                    font: appearance.codeFont
                                    color: appearance.colors.text
                                    wrapMode: TextEdit.NoWrap
                                }
                            }
                        }
                        Text {
                            anchors.centerIn: parent
                            visible: canvasHunkList.count === 0
                            text: qsTr("Nessuna differenza tra la revisione corrente e quella precedente.")
                            font: appearance.uiFont
                            color: appearance.colors.muted
                            textFormat: Text.PlainText
                        }
                    }
                }

                // Index 4: Revision History View
                ListView {
                    id: canvasHistoryList
                    objectName: "canvasHistoryList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 8
                    model: canvas.history
                    ScrollBar.vertical: KScrollBar {}
                    delegate: Rectangle {
                        required property var modelData
                        width: canvasHistoryList.width
                        implicitHeight: revRow.implicitHeight + 18
                        color: appearance.colors.surfaceAlt
                        radius: 10
                        border.color: appearance.colors.border
                        RowLayout {
                            id: revRow
                            anchors.fill: parent
                            anchors.margins: 9
                            spacing: 8
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2
                                Text {
                                    text: qsTr("Revisione %1 · %2").arg(modelData.revision || 1).arg(modelData.author === "agent" ? qsTr("Agente") : qsTr("Utente"))
                                    font: appearance.uiFont
                                    color: appearance.colors.accent
                                    textFormat: Text.PlainText
                                }
                                Text {
                                    text: modelData.summary || ""
                                    font: appearance.uiFont
                                    color: appearance.colors.text
                                    Layout.fillWidth: true
                                    wrapMode: Text.Wrap
                                    textFormat: Text.PlainText
                                }
                            }
                            KButton {
                                objectName: "canvasRestoreRev_" + modelData.revision
                                text: qsTr("Ripristina revisione")
                                subtle: true
                                enabled: !canvas.busy && modelData.revision !== canvas.currentRevision
                                onClicked: canvas.restoreRevision(modelData.revision)
                            }
                        }
                    }
                }
            }
        }
    }
}
