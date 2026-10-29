import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: overlayRoot
    objectName: "ambientOverlay"
    modal: false
    dim: true
    focus: true
    anchors.centerIn: parent
    width: Math.min(680, (parent ? parent.width : 880) - 48)
    padding: 16
    visible: ambient.overlayVisible
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    onClosed: ambient.closeSpotlight()
    onOpened: {
        spotlightInput.forceActiveFocus()
    }

    function submitPrompt() {
        var text = spotlightInput.text.trim()
        if (!text.length) return
        ambient.submitSpotlight(text)
        spotlightInput.text = ""
    }

    Connections {
        target: ambient
        function onDictationTextReady(target, text) {
            if (target === "spotlight" && !ambient.autoSendVoice) {
                var current = spotlightInput.text.trim()
                spotlightInput.text = current.length ? (current + " " + text) : text
                spotlightInput.forceActiveFocus()
            }
        }
    }

    background: Rectangle {
        color: appearance.colors.surfaceRaised
        radius: 16
        border.color: ambient.recording ? appearance.colors.danger : appearance.colors.accent
        border.width: ambient.recording ? 2 : 1
    }

    contentItem: ColumnLayout {
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            KIcon {
                name: "spotlight"
                ink: appearance.colors.accent
                width: 18
                height: 18
            }
            Text {
                objectName: "spotlightTitle"
                text: qsTr("Kira Spotlight")
                font.family: appearance.uiFont.family
                font.pixelSize: appearance.uiFont.pixelSize + 1
                font.bold: true
                color: appearance.colors.text
                textFormat: Text.PlainText
            }
            Text {
                text: qsTr("Alt+Spazio · Globale: %1").arg(ambient.spotlightCommandHint)
                font.family: appearance.uiFont.family
                font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 2)
                color: appearance.colors.muted
                textFormat: Text.PlainText
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            KButton {
                objectName: "spotlightCaptureButton"
                subtle: true
                iconName: "spotlight"
                text: qsTr("Cattura selezione")
                implicitHeight: 28
                toolTipText: qsTr("Legge il testo selezionato e il titolo della finestra attiva")
                onClicked: ambient.captureContextNow()
            }
            KButton {
                objectName: "spotlightExpandButton"
                subtle: true
                iconName: "external"
                text: qsTr("Apri in Kira")
                implicitHeight: 28
                toolTipText: qsTr("Passa alla finestra principale di Kira")
                onClicked: ambient.expandToMainWindow()
            }
            KButton {
                objectName: "spotlightCloseButton"
                subtle: true
                iconName: "close"
                implicitWidth: 28
                implicitHeight: 28
                Accessible.name: qsTr("Chiudi Spotlight")
                toolTipText: qsTr("Chiudi Spotlight (Esc)")
                onClicked: ambient.closeSpotlight()
            }
        }

        Rectangle {
            objectName: "spotlightContextChip"
            visible: ambient.hasCapturedContext
            Layout.fillWidth: true
            color: appearance.colors.surfaceAlt
            radius: 10
            border.color: appearance.colors.border
            border.width: 1
            implicitHeight: chipCol.implicitHeight + 14

            ColumnLayout {
                id: chipCol
                anchors.fill: parent
                anchors.margins: 7
                spacing: 4

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    KIcon {
                        name: "context"
                        ink: appearance.colors.accent
                        width: 15
                        height: 15
                    }
                    Text {
                        objectName: "spotlightContextChipLabel"
                        text: ambient.contextChipLabel
                        font.family: appearance.uiFont.family
                        font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 1)
                        font.bold: true
                        color: appearance.colors.text
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                    KButton {
                        objectName: "spotlightClearContextButton"
                        subtle: true
                        iconName: "close"
                        implicitWidth: 24
                        implicitHeight: 24
                        Accessible.name: qsTr("Rimuovi contesto catturato")
                        toolTipText: qsTr("Rimuovi contesto catturato")
                        onClicked: ambient.clearContext()
                    }
                }

                Text {
                    objectName: "spotlightContextPreview"
                    visible: ambient.contextPreview.length > 0
                    text: ambient.contextPreview
                    font.family: appearance.codeFont.family
                    font.pixelSize: Math.max(10, appearance.codeFont.pixelSize - 2)
                    color: appearance.colors.muted
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    maximumLineCount: 3
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            TextField {
                id: spotlightInput
                objectName: "spotlightInput"
                Layout.fillWidth: true
                font.family: appearance.uiFont.family
                font.pixelSize: appearance.uiFont.pixelSize + 1
                placeholderText: qsTr("Chiedi a Kira sulla finestra o selezione attiva…")
                Accessible.name: qsTr("Richiesta rapida Spotlight")
                selectByMouse: true
                onAccepted: overlayRoot.submitPrompt()
            }

            KButton {
                objectName: "spotlightVoiceButton"
                iconName: ambient.recording ? "stop" : "mic"
                destructive: ambient.recording
                subtle: !ambient.recording
                implicitWidth: 36
                implicitHeight: 36
                enabled: !ambient.transcribing
                Accessible.name: ambient.recording ? qsTr("Ferma dettatura vocale") : qsTr("Avvia dettatura vocale")
                toolTipText: ambient.recording ? qsTr("Ferma e trascrivi con Whisper") : qsTr("Dettatura vocale (Whisper locale / API)")
                onClicked: ambient.toggleDictation("spotlight")
            }

            KButton {
                objectName: "spotlightSendButton"
                iconName: "send"
                primary: true
                implicitWidth: 38
                implicitHeight: 36
                enabled: spotlightInput.text.trim().length > 0 && (workbench.canSubmit || workbench.goalCommandReady(spotlightInput.text))
                Accessible.name: qsTr("Invia da Spotlight")
                toolTipText: qsTr("Invia a Kira (Invio)")
                onClicked: overlayRoot.submitPrompt()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Text {
                objectName: "spotlightVoiceStatus"
                visible: ambient.voiceStatusLabel.length > 0
                text: ambient.voiceStatusLabel
                font.family: appearance.uiFont.family
                font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 2)
                color: ambient.recording ? appearance.colors.danger : appearance.colors.accent
                textFormat: Text.PlainText
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            Item {
                visible: ambient.voiceStatusLabel.length === 0
                Layout.fillWidth: true
            }
            CheckBox {
                objectName: "spotlightAutoSendSwitch"
                checked: ambient.autoSendVoice
                text: qsTr("Invio vocale automatico")
                font.family: appearance.uiFont.family
                font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 2)
                onToggled: ambient.setAutoSendVoice(checked)
            }
        }

        Rectangle {
            objectName: "spotlightResponseCard"
            visible: ambient.spotlightResponse.length > 0 || workspace.busy
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(220, Math.max(64, responseScroll.contentHeight + 20))
            color: appearance.colors.surface
            radius: 10
            border.color: appearance.colors.border
            border.width: 1

            ScrollView {
                id: responseScroll
                anchors.fill: parent
                anchors.margins: 10
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                Text {
                    objectName: "spotlightResponseText"
                    width: responseScroll.availableWidth
                    text: ambient.spotlightResponse.length > 0 ? ambient.spotlightResponse : workbench.activityLabel
                    font.family: appearance.uiFont.family
                    font.pixelSize: appearance.uiFont.pixelSize
                    color: appearance.colors.text
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                }
            }
        }
    }
}
