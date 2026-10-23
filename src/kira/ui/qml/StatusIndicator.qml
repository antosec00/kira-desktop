import QtQuick
import QtQuick.Window

Item {
    id: indicator
    state: "unknown"
    property bool animate: false
    property int diameter: 12
    property bool showSymbol: true
    readonly property color statusColor: state === "running" ? appearance.colors.accent
        : state === "completed" ? appearance.colors.success
        : state === "error" ? appearance.colors.danger
        : ["waiting", "paused", "unconfirmed"].indexOf(state) >= 0 ? appearance.colors.warning
        : appearance.colors.muted
    readonly property string stateLabel: state === "running" ? qsTr("In esecuzione")
        : state === "completed" ? qsTr("Confermato")
        : state === "error" ? qsTr("Non riuscita")
        : state === "waiting" ? qsTr("In attesa")
        : state === "paused" ? qsTr("In pausa")
        : state === "unconfirmed" ? qsTr("Esito non confermato")
        : state === "queued" ? qsTr("In coda")
        : state === "cancelled" ? qsTr("Annullato")
        : state === "idle" ? qsTr("Pronto") : qsTr("Esito sconosciuto")
    readonly property string statusSymbol: state === "completed" ? "✓"
        : state === "error" ? "!"
        : state === "waiting" ? "·"
        : state === "paused" ? "Ⅱ"
        : state === "unconfirmed" || state === "unknown" ? "?"
        : state === "queued" ? "…"
        : state === "cancelled" ? "−" : ""
    readonly property bool animating: animate && state === "running" && visible
        && !appearance.reduceMotion && !!Window.window && Window.window.visible
        && Window.window.visibility !== Window.Minimized
    implicitWidth: diameter
    implicitHeight: diameter
    Accessible.role: Accessible.Indicator
    Accessible.name: stateLabel

    Rectangle {
        id: marker
        objectName: "statusMarker"
        anchors.centerIn: parent
        width: indicator.diameter; height: width; radius: width / 2
        color: "transparent"
        border.width: 1
        border.color: indicator.statusColor
        opacity: 1
        Rectangle {
            anchors.centerIn: parent
            visible: indicator.state === "running" || !indicator.showSymbol
            width: Math.max(3, indicator.diameter * 0.42); height: width; radius: width / 2
            color: indicator.statusColor
        }
        Text {
            objectName: "statusSymbol"
            anchors.centerIn: parent
            visible: indicator.showSymbol && text.length > 0
            text: indicator.statusSymbol; textFormat: Text.PlainText
            color: indicator.statusColor
            font.family: appearance.uiFont.family
            font.pixelSize: Math.max(8, indicator.diameter - 2)
            font.weight: Font.DemiBold
            Accessible.ignored: true
        }
    }
    SequentialAnimation {
        running: indicator.animating
        loops: Animation.Infinite
        NumberAnimation { target: marker; property: "opacity"; from: 1; to: 0.42; duration: 700; easing.type: Easing.InOutSine }
        NumberAnimation { target: marker; property: "opacity"; from: 0.42; to: 1; duration: 700; easing.type: Easing.InOutSine }
        onStopped: marker.opacity = 1
    }
}
