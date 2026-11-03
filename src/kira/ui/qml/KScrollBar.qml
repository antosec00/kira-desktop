import QtQuick
import QtQuick.Controls

ScrollBar {
    id: control
    implicitWidth: 18
    implicitHeight: 18
    padding: 4
    minimumSize: Math.min(1, 36 / Math.max(1, control.horizontal ? availableWidth : availableHeight))
    policy: ScrollBar.AlwaysOn
    hoverEnabled: true
    visible: size < 1
    contentItem: Rectangle {
        objectName: "scrollThumb"
        implicitWidth: control.horizontal ? 36 : 10
        implicitHeight: control.horizontal ? 10 : 36
        radius: 5
        color: control.pressed ? appearance.colors.accent : control.hovered ? appearance.colors.text : (appearance.dark ? "#8b949e" : appearance.colors.muted)
        border.width: 1
        border.color: control.pressed ? appearance.colors.accent : (appearance.dark ? "#c9d1d9" : appearance.colors.border)
    }
    background: Rectangle {
        color: appearance.dark ? "#11161d" : appearance.colors.hover
        border.width: 1
        border.color: appearance.colors.border
        radius: 8
        opacity: control.hovered || control.pressed ? 0.95 : 0.8
    }
}
