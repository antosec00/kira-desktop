import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: pane
    objectName: "canvasPane"
    color: appearance.colors.surface
    radius: 16
    border.color: appearance.colors.border

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    objectName: "canvasPaneTitle"
                    text: canvas.currentId.length > 0 ? canvas.currentTitle : qsTr("Canvas & Artefatti Interattivi")
                    font.family: appearance.uiFont.family
                    font.pixelSize: appearance.uiFont.pixelSize + 2
                    font.weight: Font.DemiBold
                    color: appearance.colors.text
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                    textFormat: Text.PlainText
                }
                Text {
                    text: canvas.currentId.length > 0
                        ? qsTr("Tipo: %1 · Rev. %2 · %3 righe").arg(canvas.currentKind).arg(canvas.currentRevision).arg(canvas.currentLineCount)
                        : qsTr("Anteprima live Mermaid, UI HTML/Tailwind, SVG e documenti con modifica inline")
                    font.family: appearance.uiFont.family
                    font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 2)
                    color: appearance.colors.muted
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                    textFormat: Text.PlainText
                }
            }
            KButton {
                objectName: "canvasRefreshButton"
                text: qsTr("Aggiorna")
                subtle: true
                enabled: workspace.connected && !canvas.busy
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

        Flow {
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
            visible: canvas.artifacts.length > 0
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? 38 : 0
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
                        checked: canvas.currentId === modelData.id
                        subtle: canvas.currentId !== modelData.id
                        onClicked: canvas.selectArtifact(modelData.id)
                    }
                }
            }
        }

        RowLayout {
            visible: canvas.currentId.length > 0
            Layout.fillWidth: true
            spacing: 6
            KButton {
                objectName: "canvasTabPreview"
                text: qsTr("Anteprima Live")
                checked: canvas.viewMode === "preview"
                subtle: canvas.viewMode !== "preview"
                onClicked: canvas.setViewMode("preview")
            }
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
                objectName: "canvasTabDiff"
                text: qsTr("Diff Blocchi (%1)").arg(canvas.diffHunks.length)
                checked: canvas.viewMode === "diff"
                subtle: canvas.viewMode !== "diff"
                onClicked: canvas.setViewMode("diff")
            }
            KButton {
                objectName: "canvasTabHistory"
                text: qsTr("Revisioni (%1)").arg(canvas.history.length)
                checked: canvas.viewMode === "history"
                subtle: canvas.viewMode !== "history"
                onClicked: canvas.setViewMode("history")
            }
            Item { Layout.fillWidth: true }
            KButton {
                objectName: "canvasDeleteButton"
                text: qsTr("Elimina")
                subtle: true
                enabled: canvas.currentId.length > 0 && !canvas.busy
                onClicked: canvas.deleteArtifact(canvas.currentId)
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
            visible: canvas.statusText.length > 0
            text: canvas.statusText
            color: appearance.colors.accent
            font: appearance.uiFont
            wrapMode: Text.Wrap
            Layout.fillWidth: true
            textFormat: Text.PlainText
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: canvas.currentId.length === 0 ? 0
                : canvas.viewMode === "code" ? 2
                : canvas.viewMode === "diff" ? 3
                : canvas.viewMode === "history" ? 4 : 1

            Rectangle {
                color: appearance.colors.surfaceAlt
                radius: 12
                border.color: appearance.colors.border
                ColumnLayout {
                    anchors.centerIn: parent
                    width: Math.min(parent.width - 32, 420)
                    spacing: 10
                    Text {
                        text: qsTr("Nessun artefatto attivo nel Canvas.")
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
                        text: qsTr("Crea un diagramma Mermaid, un mockup HTML/Tailwind, un grafico SVG o chiedi a Kira in chat di generare un artefatto nel Canvas.")
                        font: appearance.uiFont
                        color: appearance.colors.muted
                        horizontalAlignment: Text.AlignHCenter
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }
                }
            }

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

            ColumnLayout {
                spacing: 8
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    TextField {
                        id: titleField
                        objectName: "canvasTitleField"
                        Layout.fillWidth: true
                        font: appearance.uiFont
                        selectByMouse: true
                        text: canvas.currentTitle
                        placeholderText: qsTr("Titolo dell'artefatto…")
                    }
                    KButton {
                        objectName: "canvasSaveRevisionButton"
                        text: qsTr("Salva revisione")
                        primary: true
                        enabled: canvas.currentId.length > 0 && !canvas.busy
                        onClicked: canvas.saveContent(editorArea.text, titleField.text)
                    }
                }
                ScrollView {
                    id: codeEditorScroll
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    TextArea {
                        id: editorArea
                        objectName: "canvasCodeEditor"
                        width: codeEditorScroll.availableWidth
                        font: appearance.codeFont
                        text: canvas.currentContent
                        wrapMode: TextEdit.NoWrap
                        selectByMouse: true
                        textFormat: TextEdit.PlainText
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: selectionCol.implicitHeight + 18
                    color: appearance.colors.surfaceAlt
                    radius: 10
                    border.color: appearance.colors.border
                    ColumnLayout {
                        id: selectionCol
                        anchors.fill: parent
                        anchors.margins: 9
                        spacing: 6
                        Text {
                            text: qsTr("Modifica collaborativa inline: indica l'intervallo di righe e chiedi a Kira di riscrivere solo quella sezione.")
                            font.family: appearance.uiFont.family
                            font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 2)
                            color: appearance.colors.muted
                            Layout.fillWidth: true
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Text { text: qsTr("Da riga:"); font: appearance.uiFont; color: appearance.colors.text; textFormat: Text.PlainText }
                            TextField {
                                id: selStartField
                                objectName: "canvasSelStartField"
                                Layout.preferredWidth: 64
                                font: appearance.codeFont
                                text: "1"
                                selectByMouse: true
                            }
                            Text { text: qsTr("A riga:"); font: appearance.uiFont; color: appearance.colors.text; textFormat: Text.PlainText }
                            TextField {
                                id: selEndField
                                objectName: "canvasSelEndField"
                                Layout.preferredWidth: 64
                                font: appearance.codeFont
                                text: String(Math.max(1, canvas.currentLineCount))
                                selectByMouse: true
                            }
                            TextField {
                                id: selPromptField
                                objectName: "canvasSelPromptField"
                                Layout.fillWidth: true
                                font: appearance.uiFont
                                selectByMouse: true
                                placeholderText: qsTr("Es. Ottimizza queste righe o aggiungi un nodo al diagramma…")
                            }
                            KButton {
                                objectName: "canvasAskSelectionButton"
                                text: qsTr("Chiedi a Kira sulla selezione")
                                enabled: canvas.currentId.length > 0
                                onClicked: canvas.askKiraSelection(
                                    parseInt(selStartField.text || "1"),
                                    parseInt(selEndField.text || "1"),
                                    selPromptField.text
                                )
                            }
                        }
                    }
                }
            }

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
