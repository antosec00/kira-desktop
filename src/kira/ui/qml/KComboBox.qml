import QtQuick
import QtQuick.Controls

ComboBox {
    font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
    id: control
    palette.dark: appearance.colors.muted
    hoverEnabled: true
    HoverHandler { cursorShape: appearance.pointerCursors && control.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
    background: Rectangle {
        implicitWidth: 140
        implicitHeight: Math.max(40, appearance.uiFont.pixelSize + 27)
        radius: 8
        color: !control.enabled ? appearance.colors.disabled : control.down ? appearance.colors.pressed : appearance.colors.surfaceAlt
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? appearance.colors.accent : control.hovered ? appearance.colors.accent : appearance.colors.border
    }
    // The built-in TextField display is literal; dropdown delegates also need
    // an explicit format for provider model, effort and session labels.
    delegate: ItemDelegate {
        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
        id: entry
        required property var model
        required property int index
        readonly property bool emphasized: highlighted || down || visualFocus
        width: ListView.view.width
        text: model[control.textRole]
        Accessible.name: text
        palette.base: control.palette.base
        palette.text: control.palette.text
        palette.highlight: control.palette.highlight
        palette.highlightedText: control.palette.highlightedText
        font.weight: control.currentIndex === index ? Font.DemiBold : Font.Normal
        highlighted: control.highlightedIndex === index
        hoverEnabled: control.hoverEnabled
        HoverHandler { cursorShape: appearance.pointerCursors && entry.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
        contentItem: Text {
            text: entry.text
            textFormat: Text.PlainText
            font: entry.font
            color: entry.emphasized ? entry.palette.highlightedText : entry.palette.text
            horizontalAlignment: entry.mirrored ? Text.AlignRight : Text.AlignLeft
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            implicitWidth: 100
            implicitHeight: 40
            color: entry.down ? Qt.darker(entry.palette.highlight, 1.08)
                              : entry.emphasized ? entry.palette.highlight : entry.palette.base
            border.width: entry.visualFocus ? 2 : 0
            border.color: entry.palette.highlightedText
        }
    }
}
