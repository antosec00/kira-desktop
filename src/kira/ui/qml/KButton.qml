import QtQuick
import QtQuick.Controls

Button {
    font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.italic: appearance.uiFont.italic
    id: control
    property bool primary: false
    property bool subtle: false
    property bool destructive: false
    property bool quietSelection: false
    property bool selectionMark: false
    property bool wrapText: false
    readonly property color selectionInk: appearance.colors.accent
    property string iconName: ""
    property bool leftAligned: false
    property string toolTipText: ""
    readonly property color foreground: !enabled ? appearance.colors.disabledText : primary ? appearance.colors.accentText : destructive ? appearance.colors.danger : checked ? (quietSelection ? appearance.colors.accent : appearance.colors.selectedText) : appearance.colors.text
    implicitHeight: Math.max(38, appearance.uiFont.pixelSize + 25)
    implicitWidth: Math.max(38, label.implicitWidth + 28 + (iconName ? 29 : 0))
    hoverEnabled: true
    HoverHandler { cursorShape: appearance.pointerCursors && control.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
    padding: 0
    font.pixelSize: appearance.uiFont.pixelSize
    font.weight: Math.max(Font.Medium, appearance.uiFont.weight)
    KToolTip {
        visible: control.hovered && text.length > 0
        text: control.toolTipText
    }
    contentItem: Item {
        KIcon {
            visible: control.iconName.length > 0
            name: control.iconName
            ink: control.foreground
            width: 18; height: 18
            anchors.left: parent.left; anchors.leftMargin: 10
            anchors.verticalCenter: parent.verticalCenter
        }
        Text {
            textFormat: Text.PlainText
            id: label
            anchors.fill: parent
            leftPadding: control.iconName ? 39 : control.leftAligned ? 10 : 0
            rightPadding: control.leftAligned ? 10 : 0
            text: control.text
            font: control.font
            horizontalAlignment: control.leftAligned ? Text.AlignLeft : Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            wrapMode: control.wrapText ? Text.WordWrap : Text.NoWrap
            maximumLineCount: control.wrapText ? 2 : 1
            elide: Text.ElideRight
            color: control.foreground
        }
    }
    background: Rectangle {
        radius: 10
        color: !control.enabled ? appearance.colors.disabled : control.primary ? (control.down ? appearance.colors.accentPressed : control.hovered ? appearance.colors.accentHover : appearance.colors.accent) : control.down ? appearance.colors.pressed : control.checked ? (control.quietSelection ? Qt.rgba(control.selectionInk.r, control.selectionInk.g, control.selectionInk.b, 0.13) : appearance.colors.selection) : control.hovered ? appearance.colors.hover : control.subtle ? "transparent" : appearance.colors.surfaceAlt
        border.width: control.activeFocus ? 2 : control.subtle ? 0 : 1
        border.color: control.activeFocus ? appearance.colors.accent : control.primary ? "transparent" : control.hovered ? appearance.colors.accent : appearance.colors.border
        Rectangle {
            visible: control.selectionMark && control.checked
            anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
            anchors.topMargin: 7; anchors.bottomMargin: 7
            width: 2; radius: 1; color: appearance.colors.accent
        }
    }
}
