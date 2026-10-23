import QtQuick
import QtQuick.Controls

ScrollBar {
    id: control
    implicitWidth: 18
    implicitHeight: 18
    padding: 4
    minimumSize: Math.min(1, 36 / Math.max(1, availableHeight))
    policy: ScrollBar.AlwaysOn
    hoverEnabled: true
    visible: size < 1
    contentItem: Rectangle {
        objectName: "scrollThumb"
        implicitWidth: 10; implicitHeight: 36; radius: 5
        color: control.pressed ? appearance.colors.accent : control.hovered ? appearance.colors.muted : appearance.colors.muted
    }
    background: Rectangle {
        color: appearance.colors.hover; radius: 8
        opacity: control.hovered || control.pressed ? 1 : 0.6
    }
}
