import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The guided update: what is new, then backup, verified download or source
// check, a clean stop of the idle service, the install and the restart.
KiraPanel {
    font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
    id: dialog
    objectName: "updateDialog"
    title: qsTr("Aggiornamento di Kira")
    fallbackWidth: 640
    fallbackHeight: 620
    readonly property bool busy: updates.state === "updating" || updates.state === "checking"
    readonly property bool started: updates.state === "updating" || updates.state === "failed" || updates.state === "manual"
    closePolicy: busy ? Popup.NoAutoClose : Popup.CloseOnEscape
    // Closing while the command waits keeps the installed Kira working.
    onClosed: updates.resume()

    footer: RowLayout {
        spacing: 8
        KButton {
            objectName: "updateSkip"
            Layout.leftMargin: 16; Layout.bottomMargin: 12
            visible: updates.kind === "package" && updates.state === "available"
            text: qsTr("Salta questa versione"); subtle: true
            onClicked: { updates.skip(); dialog.close() }
        }
        Item { Layout.fillWidth: true }
        KButton {
            objectName: "updateLater"
            Layout.bottomMargin: 12
            enabled: !dialog.busy
            text: updates.state === "manual" ? qsTr("Annulla") : qsTr("Più tardi")
            onClicked: dialog.close()
        }
        KButton {
            objectName: "updateRestart"
            Layout.rightMargin: 16; Layout.bottomMargin: 12
            visible: updates.state === "manual"
            text: qsTr("Riavvia Kira"); primary: true
            onClicked: updates.restart()
        }
        KButton {
            objectName: "updateInstall"
            Layout.rightMargin: 16; Layout.bottomMargin: 12
            visible: updates.state !== "manual"
            enabled: updates.canInstall && !dialog.busy && workspace.connected
            text: updates.state === "failed" ? qsTr("Riprova") : qsTr("Aggiorna ora"); primary: true
            onClicked: updates.install()
        }
    }

    contentItem: ScrollView {
        id: scroll
        clip: true
        contentWidth: availableWidth
        rightPadding: 14
        ScrollBar.vertical: KScrollBar {}

        ColumnLayout {
            width: scroll.availableWidth
            spacing: 14

            RowLayout {
                Layout.fillWidth: true
                spacing: 14
                Rectangle {
                    implicitWidth: 44; implicitHeight: 44; radius: 12
                    color: Qt.rgba(appearance.colors.accent.r, appearance.colors.accent.g, appearance.colors.accent.b, 0.14)
                    KIcon { anchors.centerIn: parent; name: "update"; width: 22; height: 22 }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text {
                        objectName: "updateHeadline"
                        Layout.fillWidth: true
                        textFormat: Text.PlainText; wrapMode: Text.Wrap
                        color: appearance.colors.text
                        font: Qt.font({family: appearance.uiFont.family, pixelSize: appearance.uiFont.pixelSize + 5, weight: Font.DemiBold})
                        text: updates.latestVersion.length
                              ? (updates.kind === "source" ? qsTr("Nuove modifiche per Kira %1").arg(updates.latestVersion)
                                                           : qsTr("Kira %1 è disponibile").arg(updates.latestVersion))
                              : qsTr("Kira è aggiornata")
                    }
                    Text {
                        Layout.fillWidth: true
                        textFormat: Text.PlainText; wrapMode: Text.Wrap
                        color: appearance.colors.muted; font: appearance.uiFont
                        text: updates.kind === "source"
                              ? qsTr("Versione installata %1 · cartella dei sorgenti, aggiornata con git e uv").arg(updates.currentVersion)
                              : qsTr("Versione installata %1 · pacchetto firmato dalla release ufficiale").arg(updates.currentVersion)
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                textFormat: Text.PlainText; wrapMode: Text.Wrap
                color: appearance.colors.textSecondary; font: appearance.uiFont
                lineHeight: 1.2
                text: qsTr("Kira fa prima un backup coerente del database e aggiorna solo quando il servizio non ha lavoro in corso. Progetti, chat e impostazioni restano dove sono.")
            }

            // What changes.
            Rectangle {
                objectName: "updateNotesBox"
                visible: updates.notes.length > 0 && !dialog.started
                Layout.fillWidth: true
                implicitHeight: Math.min(notesText.implicitHeight + 24, 260)
                radius: 10
                color: appearance.colors.surfaceAlt
                border.color: appearance.colors.border
                ScrollView {
                    id: notesScroll
                    anchors.fill: parent; anchors.margins: 12
                    clip: true
                    contentWidth: availableWidth
                    ReadText {
                        id: notesText
                        objectName: "updateNotes"
                        width: notesScroll.availableWidth
                        text: updates.notes
                    }
                }
            }
            KButton {
                visible: updates.kind !== "source" && updates.notesUrl.length > 0 && !dialog.started
                text: qsTr("Note complete della release"); iconName: "external"; subtle: true
                onClicked: Qt.openUrlExternally(updates.notesUrl)
            }

            // The steps, once the update starts.
            ColumnLayout {
                objectName: "updateSteps"
                visible: dialog.started
                Layout.fillWidth: true
                spacing: 8
                Repeater {
                    model: updates.steps
                    RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: 10
                        Rectangle {
                            implicitWidth: 22; implicitHeight: 22; radius: 11
                            color: modelData.status === "done" ? appearance.colors.success
                                 : modelData.status === "failed" ? appearance.colors.danger
                                 : modelData.status === "running" || modelData.status === "manual" ? appearance.colors.accent
                                 : "transparent"
                            border.color: modelData.status === "pending" ? appearance.colors.border : color
                            KIcon {
                                anchors.centerIn: parent; width: 14; height: 14; strokeWidth: 2.2
                                visible: modelData.status === "done" || modelData.status === "failed"
                                name: modelData.status === "done" ? "check" : "close"
                                ink: appearance.colors.background
                            }
                            BusyIndicator {
                                anchors.centerIn: parent; width: 18; height: 18
                                running: modelData.status === "running"
                                visible: running
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            textFormat: Text.PlainText; wrapMode: Text.Wrap; font: appearance.uiFont
                            color: modelData.status === "pending" ? appearance.colors.muted : appearance.colors.text
                            text: modelData.label
                        }
                    }
                }
            }

            Text {
                objectName: "updateMessage"
                visible: text.length > 0
                Layout.fillWidth: true
                textFormat: Text.PlainText; wrapMode: Text.Wrap; font: appearance.uiFont
                color: updates.state === "failed" || updates.state === "error" ? appearance.colors.danger : appearance.colors.textSecondary
                text: updates.message
            }

            // The command to run when Kira cannot ask for the password itself.
            RowLayout {
                objectName: "updateManual"
                visible: updates.state === "manual"
                Layout.fillWidth: true
                spacing: 8
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: commandText.implicitHeight + 20
                    radius: 8
                    color: appearance.colors.codeBackground
                    border.color: appearance.colors.border
                    ReadText {
                        id: commandText
                        objectName: "updateCommand"
                        anchors.fill: parent; anchors.margins: 10
                        font: appearance.codeFont
                        color: appearance.colors.codeText
                        text: updates.manualCommand
                    }
                }
                KButton {
                    objectName: "updateCopyCommand"
                    iconName: "copy"; toolTipText: qsTr("Copia il comando")
                    Accessible.name: qsTr("Copia il comando")
                    onClicked: updates.copyCommand()
                }
            }

            Text {
                visible: updates.kind === "other"
                Layout.fillWidth: true
                textFormat: Text.PlainText; wrapMode: Text.Wrap
                color: appearance.colors.muted; font: appearance.uiFont
                text: qsTr("Questa installazione non viene da un pacchetto né da una cartella git: scarica la nuova versione dalla pagina delle release.")
            }
        }
    }
}
