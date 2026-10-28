import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "mcpServersPane"
    readonly property var mcp: workspace.mcp
    property bool importVisible: false
    property bool editorVisible: false
    property string editingId: ""

    Layout.fillWidth: true
    implicitHeight: content.implicitHeight + 32
    radius: 12
    color: appearance.colors.surfaceAlt
    border.color: appearance.colors.border

    Connections {
        target: root.mcp
        function onImportSucceeded() {
            root.importVisible = false
            mcpJsonInput.text = ""
        }
        function onSaveSucceeded() {
            root.editorVisible = false
            root.editingId = ""
        }
    }

    function openNewServer() {
        root.mcp.clearMessages()
        root.importVisible = false
        root.editingId = ""
        mcpNameInput.text = ""
        mcpTransportChoice.currentIndex = 0
        mcpEndpointInput.text = ""
        mcpArgsInput.text = ""
        mcpSecretsInput.text = ""
        mcpReadOnlyToggle.checked = false
        root.editorVisible = true
    }

    function openEditServer(server) {
        root.mcp.clearMessages()
        root.importVisible = false
        root.editingId = String(server.id || "")
        mcpNameInput.text = String(server.name || "")
        mcpTransportChoice.currentIndex = server.transport === "http" ? 1 : 0
        mcpEndpointInput.text = server.transport === "http" ? String(server.url || "") : String(server.command || "")
        mcpArgsInput.text = String(server.argsText || "")
        mcpSecretsInput.text = ""
        mcpReadOnlyToggle.checked = Boolean(server.read_only)
        root.editorVisible = true
    }

    component Note: Text {
        Layout.fillWidth: true
        textFormat: Text.PlainText
        font: appearance.uiFont
        color: appearance.colors.muted
        wrapMode: Text.Wrap
    }

    component FieldLabel: Text {
        Layout.fillWidth: true
        textFormat: Text.PlainText
        font: appearance.uiFont
        color: appearance.colors.text
        wrapMode: Text.Wrap
    }

    ColumnLayout {
        id: content
        anchors.fill: parent
        anchors.margins: 16
        spacing: 12

        Note {
            text: qsTr("Server MCP esterni · Model Context Protocol")
            color: appearance.colors.text
            font: Qt.font({family: appearance.uiFont.family, pixelSize: appearance.uiFont.pixelSize,
                           weight: Font.DemiBold, italic: appearance.uiFont.italic})
        }
        Note {
            text: qsTr("Collega server MCP di terze parti (GitHub, Postgres, Notion, Sentry, Linear o server locali) tramite stdio o HTTP/SSE. Gli strumenti rilevati sono disponibili in tutte le chat con kira.mcp_tools.")
        }

        Flow {
            Layout.fillWidth: true
            spacing: 8
            KButton {
                objectName: "mcpAddServerButton"
                text: qsTr("Aggiungi server MCP")
                primary: !root.editorVisible && !root.importVisible
                enabled: root.mcp.editable
                onClicked: {
                    if (root.editorVisible && root.editingId === "") {
                        root.editorVisible = false
                    } else {
                        root.openNewServer()
                    }
                }
            }
            KButton {
                objectName: "mcpImportJsonButton"
                text: qsTr("Importa JSON mcpServers")
                enabled: root.mcp.editable
                onClicked: {
                    root.mcp.clearMessages()
                    root.editorVisible = false
                    root.importVisible = !root.importVisible
                }
            }
            KButton {
                objectName: "mcpRefreshButton"
                text: qsTr("Aggiorna elenco")
                subtle: true
                enabled: workspace.connected && !root.mcp.busy
                onClicked: root.mcp.refresh()
            }
        }

        Rectangle {
            objectName: "mcpImportPanel"
            visible: root.importVisible
            Layout.fillWidth: true
            implicitHeight: importLayout.implicitHeight + 24
            radius: 8
            color: appearance.colors.surface
            border.color: appearance.colors.border

            ColumnLayout {
                id: importLayout
                anchors.fill: parent
                anchors.margins: 12
                spacing: 10
                FieldLabel {
                    text: qsTr("Incolla una configurazione JSON compatibile con Claude Desktop o Cursor (mcpServers)")
                }
                TextArea {
                    id: mcpJsonInput
                    objectName: "mcpJsonInput"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 120
                    font: appearance.codeFont
                    color: appearance.colors.text
                    wrapMode: TextEdit.Wrap
                    selectByMouse: true
                    placeholderText: "{\"mcpServers\": {\"github\": {\"command\": \"npx\", \"args\": [\"-y\", \"@modelcontextprotocol/server-github\"]}}}"
                    background: Rectangle {
                        radius: 6
                        color: appearance.colors.surfaceAlt
                        border.color: appearance.colors.border
                    }
                    Accessible.name: qsTr("Configurazione JSON mcpServers")
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: 8
                    KButton {
                        objectName: "mcpApplyImportButton"
                        text: qsTr("Importa configurazione")
                        primary: true
                        enabled: root.mcp.editable
                        onClicked: root.mcp.importJson(mcpJsonInput.text)
                    }
                    KButton {
                        objectName: "mcpCancelImportButton"
                        text: qsTr("Annulla")
                        subtle: true
                        onClicked: root.importVisible = false
                    }
                }
            }
        }

        Rectangle {
            objectName: "mcpEditorPanel"
            visible: root.editorVisible
            Layout.fillWidth: true
            implicitHeight: editorLayout.implicitHeight + 24
            radius: 8
            color: appearance.colors.surface
            border.color: appearance.colors.border

            ColumnLayout {
                id: editorLayout
                anchors.fill: parent
                anchors.margins: 12
                spacing: 10

                FieldLabel {
                    text: root.editingId.length > 0 ? qsTr("Modifica server MCP") : qsTr("Nuovo server MCP")
                    font: Qt.font({family: appearance.uiFont.family, pixelSize: appearance.uiFont.pixelSize,
                                   weight: Font.DemiBold, italic: appearance.uiFont.italic})
                }
                FieldLabel { text: qsTr("Nome del server") }
                TextField {
                    id: mcpNameInput
                    objectName: "mcpNameInput"
                    Layout.fillWidth: true
                    selectByMouse: true
                    placeholderText: "es. GitHub, Postgres, Sentry"
                    Accessible.name: qsTr("Nome del server MCP")
                }
                FieldLabel { text: qsTr("Trasporto") }
                KComboBox {
                    id: mcpTransportChoice
                    objectName: "mcpTransportChoice"
                    Layout.fillWidth: true
                    model: [qsTr("stdio · comando locale"), qsTr("HTTP / SSE · endpoint remoto")]
                    Accessible.name: qsTr("Trasporto del server MCP")
                }
                FieldLabel {
                    text: mcpTransportChoice.currentIndex === 1 ? qsTr("URL endpoint HTTP / SSE") : qsTr("Comando eseguibile")
                }
                TextField {
                    id: mcpEndpointInput
                    objectName: "mcpEndpointInput"
                    Layout.fillWidth: true
                    font: appearance.codeFont
                    selectByMouse: true
                    placeholderText: mcpTransportChoice.currentIndex === 1 ? "https://example.com/mcp" : "npx"
                    Accessible.name: mcpTransportChoice.currentIndex === 1 ? qsTr("URL endpoint MCP") : qsTr("Comando eseguibile MCP")
                }
                FieldLabel {
                    visible: mcpTransportChoice.currentIndex === 0
                    text: qsTr("Argomenti comando (separati da spazio)")
                }
                TextField {
                    id: mcpArgsInput
                    objectName: "mcpArgsInput"
                    visible: mcpTransportChoice.currentIndex === 0
                    Layout.fillWidth: true
                    font: appearance.codeFont
                    selectByMouse: true
                    placeholderText: "-y @modelcontextprotocol/server-github"
                    Accessible.name: qsTr("Argomenti comando MCP")
                }
                FieldLabel {
                    text: mcpTransportChoice.currentIndex === 1
                        ? qsTr("Header HTTP opzionali (CHIAVE=VALORE, uno per riga)")
                        : qsTr("Variabili d’ambiente opzionali (CHIAVE=VALORE, una per riga)")
                }
                TextArea {
                    id: mcpSecretsInput
                    objectName: "mcpSecretsInput"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 68
                    font: appearance.codeFont
                    color: appearance.colors.text
                    wrapMode: TextEdit.Wrap
                    selectByMouse: true
                    placeholderText: mcpTransportChoice.currentIndex === 1 ? "Authorization=Bearer ..." : "GITHUB_PERSONAL_ACCESS_TOKEN=..."
                    background: Rectangle {
                        radius: 6
                        color: appearance.colors.surfaceAlt
                        border.color: appearance.colors.border
                    }
                    Accessible.name: qsTr("Variabili o header del server MCP")
                }
                CheckBox {
                    id: mcpReadOnlyToggle
                    objectName: "mcpReadOnlyToggle"
                    text: qsTr("Server di sola lettura (consentito anche in Pianifica e Revisiona)")
                    checked: false
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: 8
                    KButton {
                        objectName: "mcpSaveServerButton"
                        text: qsTr("Salva server MCP")
                        primary: true
                        enabled: root.mcp.editable
                        onClicked: root.mcp.saveServer(
                            root.editingId,
                            mcpNameInput.text,
                            mcpTransportChoice.currentIndex === 1 ? "http" : "stdio",
                            mcpEndpointInput.text,
                            mcpArgsInput.text,
                            mcpSecretsInput.text,
                            mcpReadOnlyToggle.checked
                        )
                    }
                    KButton {
                        objectName: "mcpCancelEditorButton"
                        text: qsTr("Annulla")
                        subtle: true
                        onClicked: root.editorVisible = false
                    }
                }
            }
        }

        Note {
            objectName: "mcpStatusMessage"
            visible: text.length > 0
            text: root.mcp.statusMessage
            color: appearance.colors.text
        }
        Note {
            objectName: "mcpErrorMessage"
            visible: text.length > 0
            text: root.mcp.error
            color: appearance.colors.danger
        }

        Note {
            objectName: "mcpEmptyNotice"
            visible: root.mcp.count === 0 && !root.editorVisible && !root.importVisible
            text: qsTr("Nessun server MCP esterno configurato.")
        }

        Repeater {
            model: root.mcp.servers
            delegate: Rectangle {
                required property var modelData
                objectName: "mcpServerCard_" + modelData.id
                Layout.fillWidth: true
                implicitHeight: cardLayout.implicitHeight + 24
                radius: 8
                color: appearance.colors.surface
                border.color: appearance.colors.border

                ColumnLayout {
                    id: cardLayout
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 8

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Text {
                            text: modelData.name
                            textFormat: Text.PlainText
                            color: appearance.colors.text
                            font: Qt.font({family: appearance.uiFont.family, pixelSize: appearance.uiFont.pixelSize,
                                           weight: Font.DemiBold, italic: appearance.uiFont.italic})
                            elide: Text.ElideRight
                        }
                        Rectangle {
                            radius: 6
                            color: appearance.colors.surfaceAlt
                            border.color: appearance.colors.border
                            implicitWidth: transportBadge.implicitWidth + 12
                            implicitHeight: transportBadge.implicitHeight + 4
                            Text {
                                id: transportBadge
                                anchors.centerIn: parent
                                text: modelData.transportLabel
                                textFormat: Text.PlainText
                                font: Qt.font({family: appearance.codeFont.family, pixelSize: Math.max(10, appearance.uiFont.pixelSize - 2)})
                                color: appearance.colors.muted
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: modelData.statusLabel
                            textFormat: Text.PlainText
                            font: appearance.uiFont
                            color: modelData.status === "error" ? appearance.colors.danger
                                 : modelData.status === "ready" ? appearance.colors.accent
                                 : appearance.colors.muted
                            elide: Text.ElideRight
                        }
                        KButton {
                            objectName: "mcpToggleEnabled_" + modelData.id
                            text: modelData.enabled ? qsTr("Attivo") : qsTr("Disattivato")
                            checked: modelData.enabled
                            subtle: true
                            enabled: root.mcp.editable
                            onClicked: root.mcp.toggleEnabled(modelData.id, !modelData.enabled)
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: modelData.endpointSummary
                        textFormat: Text.PlainText
                        font: appearance.codeFont
                        color: appearance.colors.muted
                        wrapMode: Text.WrapAnywhere
                    }

                    Note {
                        visible: modelData.toolCount > 0
                        text: qsTr("Strumenti rilevati: %1").arg(modelData.toolsText)
                        color: appearance.colors.text
                    }

                    Note {
                        visible: modelData.lastError.length > 0
                        text: modelData.lastError
                        color: appearance.colors.danger
                    }

                    Flow {
                        Layout.fillWidth: true
                        spacing: 8
                        KButton {
                            objectName: "mcpTestServer_" + modelData.id
                            text: root.mcp.testingServerId === modelData.id
                                ? qsTr("Verifica in corso…")
                                : qsTr("Verifica e rileva strumenti")
                            enabled: root.mcp.editable
                            onClicked: root.mcp.testServer(modelData.id)
                        }
                        KButton {
                            objectName: "mcpEditServer_" + modelData.id
                            text: qsTr("Modifica")
                            subtle: true
                            enabled: root.mcp.editable
                            onClicked: root.openEditServer(modelData)
                        }
                        KButton {
                            objectName: "mcpDeleteServer_" + modelData.id
                            text: qsTr("Rimuovi")
                            subtle: true
                            enabled: root.mcp.editable
                            onClicked: root.mcp.deleteServer(modelData.id)
                        }
                    }
                }
            }
        }
    }
}
