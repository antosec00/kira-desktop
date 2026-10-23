import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: pane
    objectName: "studioPane"
    readonly property var studio: workspace.studio
    color: appearance.colors.surface
    border.color: appearance.colors.border
    border.width: 1

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 9

        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            Label {
                text: qsTr("Kira Studio")
                font.family: appearance.uiFont.family
                font.pixelSize: appearance.uiFont.pixelSize
                font.weight: Font.DemiBold
                color: appearance.colors.text
            }
            Label {
                text: pane.studio.enabled ? qsTr("Attivo nel progetto") : qsTr("Spento")
                color: pane.studio.enabled ? appearance.colors.accent : appearance.colors.muted
                font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 2)
            }
            Item { Layout.fillWidth: true }
            KButton {
                objectName: "studioExpand"
                text: pane.studio.expanded ? "↙" : "↗"
                subtle: true
                implicitWidth: 34
                toolTipText: pane.studio.expanded ? qsTr("Affianca alla chat") : qsTr("Espandi Studio")
                Accessible.name: toolTipText
                onClicked: pane.studio.toggleExpanded()
            }
            KButton {
                objectName: "studioHide"
                text: "×"
                subtle: true
                implicitWidth: 34
                toolTipText: qsTr("Nascondi Studio senza eliminare il lavoro")
                Accessible.name: toolTipText
                onClicked: pane.studio.hideStudio()
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: appearance.colors.border }

        Label {
            objectName: "studioError"
            visible: pane.studio.error.length > 0
            text: pane.studio.error
            textFormat: Text.PlainText
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            color: appearance.colors.danger
        }

        ColumnLayout {
            visible: !pane.studio.enabled
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12
            Item { Layout.fillHeight: true }
            Label {
                text: qsTr("Il tuo mondo creativo, privato e persistente.")
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                font.pixelSize: appearance.uiFont.pixelSize + 4
                color: appearance.colors.text
            }
            Label {
                text: qsTr("Studio è disattivato per impostazione predefinita. Attivalo soltanto quando vuoi costruire una scena. Il browser rimane indipendente.")
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                color: appearance.colors.muted
            }
            KButton {
                objectName: "studioEnable"
                text: qsTr("Attiva Studio in questo progetto")
                Layout.alignment: Qt.AlignHCenter
                primary: true
                enabled: !pane.studio.busy
                onClicked: pane.studio.setEnabled(true)
            }
            Item { Layout.fillHeight: true }
        }

        ColumnLayout {
            visible: pane.studio.enabled
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 10

            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: qsTr("Poster 2D · scena modificabile")
                    color: appearance.colors.muted
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
                KButton {
                    objectName: "studioDisable"
                    text: qsTr("Disattiva")
                    subtle: true
                    onClicked: pane.studio.setEnabled(false)
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Label {
                    Layout.fillWidth: true
                    text: pane.studio.agentEnabled
                          ? qsTr("Kira può modificare questo Studio dalla chat.")
                          : pane.studio.agentEligible
                          ? qsTr("Puoi autorizzare Kira a creare e modificare dalla chat.")
                          : qsTr("Le modifiche IA richiedono un progetto scrivibile e strumenti attivi nella chat.")
                    color: appearance.colors.muted
                    wrapMode: Text.WordWrap
                }
                KButton {
                    objectName: "studioAgentGrant"
                    text: pane.studio.agentEnabled
                          ? qsTr("Revoca accesso IA") : qsTr("Consenti modifiche IA")
                    checked: pane.studio.agentEnabled
                    enabled: !pane.studio.busy
                             && (pane.studio.agentEligible || pane.studio.agentEnabled)
                    onClicked: pane.studio.setAgentEnabled(!pane.studio.agentEnabled)
                    toolTipText: qsTr("Permesso specifico per questa chat. Non concede accesso al sistema.")
                }
            }

            RowLayout {
                Layout.fillWidth: true
                TextField {
                    id: captionInput
                    objectName: "studioCaption"
                    Layout.fillWidth: true
                    text: ""
                    maximumLength: 90
                    placeholderText: qsTr("Scrivi il testo del poster")
                    selectByMouse: true
                    color: appearance.colors.text
                    font: appearance.uiFont
                }
                Connections {
                    target: pane.studio
                    function onChanged() {
                        if (!captionInput.activeFocus && pane.studio.posterText.length > 0)
                            captionInput.text = pane.studio.posterText
                    }
                }
                KButton {
                    objectName: "studioCreatePoster"
                    text: qsTr("Crea")
                    primary: true
                    enabled: !pane.studio.busy && captionInput.text.length > 0
                    onClicked: pane.studio.createPoster(captionInput.text)
                }
                KButton {
                    objectName: "studioChangeText"
                    text: qsTr("Modifica")
                    enabled: !pane.studio.busy && pane.studio.revision > 0 && captionInput.text.length > 0
                    onClicked: pane.studio.changeText(captionInput.text)
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 5
                KButton {
                    objectName: "studioBrushBlue"
                    text: qsTr("Blu")
                    enabled: !pane.studio.busy && pane.studio.revision > 0
                    onClicked: pane.studio.changeTextColor("#2467c9")
                }
                KButton {
                    objectName: "studioBrushNavy"
                    text: qsTr("Blu notte")
                    enabled: !pane.studio.busy && pane.studio.revision > 0
                    onClicked: pane.studio.changeTextColor("#182b68")
                }
                KButton {
                    objectName: "studioBackgroundLilac"
                    text: qsTr("Lilla")
                    enabled: !pane.studio.busy && pane.studio.revision > 0
                    onClicked: pane.studio.changeBackground("#f0e7f3")
                }
                KButton {
                    objectName: "studioBackgroundCream"
                    text: qsTr("Crema")
                    enabled: !pane.studio.busy && pane.studio.revision > 0
                    onClicked: pane.studio.changeBackground("#fff1dc")
                }
                Item { Layout.fillWidth: true }
            }

            Rectangle {
                objectName: "studioCanvas"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 150
                color: appearance.colors.background
                radius: 8
                border.color: appearance.colors.border
                Image {
                    id: renderedScene
                    objectName: "studioPreview"
                    anchors.fill: parent
                    anchors.margins: 12
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                    cache: false
                    source: pane.studio.previewUrl
                    visible: source.toString().length > 0
                }
                Label {
                    anchors.centerIn: parent
                    width: Math.min(300, parent.width - 30)
                    visible: !renderedScene.visible
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                    text: pane.studio.busy ? qsTr("Sto preparando l'anteprima…")
                         : qsTr("Crea un poster per iniziare.")
                    color: appearance.colors.muted
                }
            }

            RowLayout {
                Layout.fillWidth: true
                KButton {
                    objectName: "studioMoveLeft"
                    toolTipText: qsTr("Sposta il testo a sinistra")
                    Accessible.name: toolTipText
                    text: "←"
                    subtle: true
                    enabled: !pane.studio.busy && pane.studio.revision > 0
                    onClicked: pane.studio.moveText(-18, 0)
                }
                KButton {
                    objectName: "studioMoveRight"
                    toolTipText: qsTr("Sposta il testo a destra")
                    Accessible.name: toolTipText
                    text: "→"
                    subtle: true
                    enabled: !pane.studio.busy && pane.studio.revision > 0
                    onClicked: pane.studio.moveText(18, 0)
                }
                KButton {
                    objectName: "studioMoveUp"
                    toolTipText: qsTr("Sposta il testo in alto")
                    Accessible.name: toolTipText
                    text: "↑"
                    subtle: true
                    enabled: !pane.studio.busy && pane.studio.revision > 0
                    onClicked: pane.studio.moveText(0, -18)
                }
                KButton {
                    objectName: "studioMoveDown"
                    toolTipText: qsTr("Sposta il testo in basso")
                    Accessible.name: toolTipText
                    text: "↓"
                    subtle: true
                    enabled: !pane.studio.busy && pane.studio.revision > 0
                    onClicked: pane.studio.moveText(0, 18)
                }
                Label {
                    text: qsTr("Rev. %1").arg(pane.studio.revision)
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignRight
                    color: appearance.colors.muted
                }
                KButton {
                    objectName: "studioExport"
                    text: qsTr("Esporta PNG")
                    enabled: !pane.studio.busy && pane.studio.revision > 0
                    onClicked: pane.studio.exportPng()
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: qsTr("Ripristina versione:")
                    color: appearance.colors.muted
                }
                Repeater {
                    model: Math.min(4, pane.studio.history.length)
                    KButton {
                        text: String(pane.studio.history[index].revision)
                        subtle: true
                        enabled: !pane.studio.busy
                                 && pane.studio.history[index].revision !== pane.studio.revision
                        onClicked: pane.studio.restore(pane.studio.history[index].revision)
                    }
                }
                Item { Layout.fillWidth: true }
            }
            Label {
                objectName: "studioExportPath"
                visible: pane.studio.exportPath.length > 0
                Layout.fillWidth: true
                text: qsTr("PNG salvato in: %1").arg(pane.studio.exportPath)
                textFormat: Text.PlainText
                color: appearance.colors.muted
                wrapMode: Text.WrapAnywhere
            }
        }
    }
}
