import QtQuick
import QtQuick.Controls

TextArea {
    font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic
    readOnly: true
    selectByMouse: true
    textFormat: TextEdit.PlainText
    wrapMode: TextEdit.Wrap
    color: appearance.colors.text
    selectionColor: appearance.colors.selection
    selectedTextColor: appearance.colors.selectedText
    font.pixelSize: appearance.uiFont.pixelSize
    padding: 0
    background: null
    persistentSelection: true
    Accessible.role: Accessible.StaticText
}
