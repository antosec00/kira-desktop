import QtQuick

// Kira's mascot with a soft glow, a turning ring and a gentle float. When
// animated is false (reduced motion, the launch window) everything stays still.
Item {
    id: art
    property bool animated: true
    property real size: 150
    property string version: ""
    readonly property color accent: appearance.colors.accent
    implicitWidth: Math.max(size + 44, wordmark.implicitWidth)
    implicitHeight: size + 44 + 14 + wordmark.implicitHeight

    Item {
        id: stage
        anchors.horizontalCenter: parent.horizontalCenter
        width: art.size + 44; height: width

        Rectangle {
            id: glow
            anchors.centerIn: parent
            width: art.size + 24; height: width; radius: width / 2
            color: Qt.rgba(art.accent.r, art.accent.g, art.accent.b, 0.12)
            opacity: 0.8
            SequentialAnimation on opacity {
                running: art.animated; loops: Animation.Infinite
                NumberAnimation { to: 1; duration: 1500; easing.type: Easing.InOutSine }
                NumberAnimation { to: 0.55; duration: 1500; easing.type: Easing.InOutSine }
            }
        }
        Canvas {
            id: ring
            anchors.fill: parent
            onPaint: {
                const c = getContext("2d")
                c.reset()
                c.lineWidth = 3
                c.lineCap = "round"
                const radius = width / 2 - 3
                c.strokeStyle = Qt.rgba(art.accent.r, art.accent.g, art.accent.b, 0.18)
                c.beginPath(); c.arc(width / 2, height / 2, radius, 0, Math.PI * 2); c.stroke()
                c.strokeStyle = art.accent
                c.beginPath(); c.arc(width / 2, height / 2, radius, -Math.PI / 2, Math.PI * 0.25); c.stroke()
            }
            onWidthChanged: requestPaint()
            Connections { target: art; function onAccentChanged() { ring.requestPaint() } }
            RotationAnimator on rotation { running: art.animated; from: 0; to: 360; duration: 1600; loops: Animation.Infinite }
        }
        Image {
            objectName: "loadingMascot"
            anchors.centerIn: parent
            width: art.size; height: art.size
            source: "../assets/kira-mascot.png"
            sourceSize.width: art.size * 2; sourceSize.height: art.size * 2
            fillMode: Image.PreserveAspectFit
            smooth: true; mipmap: true
            Accessible.role: Accessible.Graphic
            Accessible.name: qsTr("Kira, mascotte dell'applicazione")
            transform: Translate { id: bob }
            SequentialAnimation {
                running: art.animated; loops: Animation.Infinite
                NumberAnimation { target: bob; property: "y"; to: -5; duration: 1500; easing.type: Easing.InOutSine }
                NumberAnimation { target: bob; property: "y"; to: 3; duration: 1500; easing.type: Easing.InOutSine }
            }
        }
    }

    Column {
        id: wordmark
        anchors.top: stage.bottom; anchors.topMargin: 14
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 2
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "kira"; textFormat: Text.PlainText
            color: appearance.colors.text
            font.family: appearance.uiFont.family; font.pixelSize: Math.round(art.size * 0.24); font.weight: Font.Bold
            font.letterSpacing: 1
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "DESKTOP" + (art.version.length ? "  /  " + art.version : ""); textFormat: Text.PlainText
            color: appearance.colors.muted
            font.family: appearance.uiFont.family; font.pixelSize: Math.max(9, Math.round(art.size * 0.075)); font.letterSpacing: 2
        }
    }
}
