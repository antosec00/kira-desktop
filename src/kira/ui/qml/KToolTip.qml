import QtQuick
import QtQuick.Controls

ToolTip {
    font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
    id: control
    objectName: "literalToolTip"
    background: Rectangle { color: appearance.colors.surfaceAlt; border.color: appearance.colors.border; radius: 6 }
    contentItem: Text {
        text: control.text
        textFormat: Text.PlainText
        font: control.font
        wrapMode: Text.Wrap
        color: control.palette.toolTipText
    }
}
