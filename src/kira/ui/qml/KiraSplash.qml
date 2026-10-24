import QtQuick
import QtQuick.Window

// A small window shown at launch while the main interface is being built;
// the main window's loading screen takes over with the same picture.
Window {
    id: splash
    objectName: "kiraSplashWindow"
    flags: Qt.SplashScreen | Qt.FramelessWindowHint
    width: 400; height: 340
    color: appearance.colors.background
    title: "Kira Desktop"
    property string version: ""
    Component.onCompleted: {
        x = Screen.virtualX + (Screen.width - width) / 2
        y = Screen.virtualY + (Screen.height - height) / 2
        visible = true
    }
    Rectangle { anchors.fill: parent; color: "transparent"; border.color: appearance.colors.border; border.width: 1 }
    KiraLoadingArt {
        anchors.centerIn: parent
        size: 132
        version: splash.version
        animated: false
    }
}
