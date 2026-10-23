import QtQuick
import QtQuick.Layouts

RowLayout {
    id: indicator
    property string label: ""
    property bool animate: false
    property bool motionEnabled: false
    property string outcome: "unknown"
    property string startedAt: ""
    property bool clockEnabled: false
    property int elapsedSeconds: 0
    readonly property real startedMilliseconds: Date.parse(startedAt)
    readonly property bool clockRunning: clockEnabled && visible && isFinite(startedMilliseconds)
    readonly property string elapsedText: {
        function pad(value) { return String(value).padStart(2, "0") }
        var hours = Math.floor(elapsedSeconds / 3600)
        return (hours > 0 ? pad(hours) + ":" : "") + pad(Math.floor(elapsedSeconds / 60) % 60) + ":" + pad(elapsedSeconds % 60)
    }
    readonly property bool animating: activityStatus.animating
    KFormat { id: kformat }
    spacing: 7
    function updateElapsed() {
        elapsedSeconds = isFinite(startedMilliseconds) ? Math.max(0, Math.floor((Date.now() - startedMilliseconds) / 1000)) : 0
    }
    onStartedMillisecondsChanged: updateElapsed()
    onClockRunningChanged: if (clockRunning) updateElapsed()
    Timer {
        objectName: "activityElapsedTimer"
        interval: 1000; repeat: true; running: indicator.clockRunning
        onTriggered: indicator.updateElapsed()
    }
    Image {
        objectName: "activityMascot"
        Layout.preferredWidth: 27; Layout.preferredHeight: 27
        source: "../assets/kira-mascot.png"
        sourceSize.width: 64; sourceSize.height: 64
        fillMode: Image.PreserveAspectFit; smooth: true; mipmap: true
        Accessible.ignored: true
    }
    StatusIndicator {
        id: activityStatus
        objectName: "activityStatus"
        state: indicator.outcome
        animate: indicator.animate && indicator.motionEnabled
        Accessible.ignored: true
    }
    Text {
        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic
        objectName: "activityLabel"
        Layout.fillWidth: true; Layout.minimumWidth: 0
        text: indicator.label; textFormat: Text.PlainText
        color: ["running", "error", "waiting", "paused", "unconfirmed"].indexOf(indicator.outcome) >= 0
            ? activityStatus.statusColor : appearance.colors.muted
        font.pixelSize: appearance.uiFont.pixelSize; elide: Text.ElideRight
        Accessible.name: indicator.label
        KToolTip { visible: activityHover.containsMouse; text: indicator.label }
        MouseArea { id: activityHover; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }
    }
    Text {
        objectName: "activityElapsed"
        visible: isFinite(indicator.startedMilliseconds)
        text: indicator.elapsedText; textFormat: Text.PlainText
        color: appearance.colors.muted
        font.family: appearance.codeFont.family
        font.pixelSize: Math.max(10, appearance.uiFont.pixelSize - 2)
        Accessible.name: qsTr("Tempo trascorso dall’avvio della richiesta: %1").arg(text)
        KToolTip {
            id: elapsedTip
            visible: elapsedHover.containsMouse
            text: qsTr("Tempo trascorso dall’avvio della richiesta") + "\n" + kformat.timestamp(indicator.startedAt)
        }
        MouseArea { id: elapsedHover; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }
    }
}
