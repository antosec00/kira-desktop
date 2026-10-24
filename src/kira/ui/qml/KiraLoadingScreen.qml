import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Shown while Kira starts. It follows the real stages (service, projects, the
// selected provider's models) and fades out when the window is ready to use.
// A modal popup, so nothing behind it reacts to the pointer or the keyboard.
Popup {
    id: screen
    objectName: "kiraLoadingScreen"
    readonly property string stage: workspace.startupStage
    readonly property bool active: stage !== "ready"
    readonly property int step: stage === "service" ? 0 : stage === "projects" ? 1 : 2
    property bool slow: false

    parent: Overlay.overlay
    x: 0; y: 0
    width: parent ? parent.width : 0
    height: parent ? parent.height : 0
    z: 10000
    padding: 0
    modal: true
    dim: false
    focus: true
    closePolicy: Popup.NoAutoClose
    visible: active
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: appearance.reduceMotion ? 0 : 420; easing.type: Easing.OutCubic } }
    background: Rectangle {
        color: appearance.colors.background
        Accessible.role: Accessible.StatusBar
        Accessible.name: status.text
    }

    KFormat { id: kformat }
    Timer { interval: 6000; running: screen.active; onTriggered: screen.slow = true }

    contentItem: Item {
        ColumnLayout {
            anchors.centerIn: parent
            width: Math.min(parent.width - 48, 440)
            spacing: 18

            KiraLoadingArt {
                Layout.alignment: Qt.AlignHCenter
                size: Math.min(150, screen.height * 0.2)
                version: workspace.appVersion
                animated: screen.active && !appearance.reduceMotion
            }

            Text {
                id: status
                objectName: "loadingStatus"
                Layout.fillWidth: true; Layout.topMargin: 6
                horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap; textFormat: Text.PlainText
                color: appearance.colors.textSecondary
                font.family: appearance.uiFont.family; font.pixelSize: appearance.uiFont.pixelSize + 1
                text: screen.stage === "service" ? workspace.connectionText
                      : screen.stage === "projects" ? qsTr("Carico progetti e chat…")
                      : qsTr("Chiedo i modelli a %1…").arg(kformat.providerLabel(workspace.backend))
            }

            // Three steps: service, projects, models.
            Row {
                objectName: "loadingSteps"
                Layout.alignment: Qt.AlignHCenter
                spacing: 8
                Repeater {
                    model: 3
                    Rectangle {
                        required property int index
                        width: index === screen.step ? 22 : 8; height: 8; radius: 4
                        color: index <= screen.step ? appearance.colors.accent : appearance.colors.border
                        Behavior on width { NumberAnimation { duration: appearance.reduceMotion ? 0 : 220; easing.type: Easing.OutCubic } }
                    }
                }
            }

            Text {
                visible: screen.slow
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap; textFormat: Text.PlainText
                color: appearance.colors.muted; font: appearance.uiFont
                text: qsTr("Ci vuole più del solito. Puoi entrare comunque: Kira finisce di caricare in background.")
            }
            KButton {
                objectName: "loadingContinue"
                visible: screen.slow
                Layout.alignment: Qt.AlignHCenter
                text: qsTr("Continua senza attendere")
                subtle: true
                onClicked: workspace.finishStartupScreen()
            }
        }
    }
}
