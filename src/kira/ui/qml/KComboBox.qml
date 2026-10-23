import QtQuick
import QtQuick.Controls

ComboBox {
    font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
    id: control
    // A chip is a compact pill for the composer; the popup keeps the full width.
    property bool chip: false
    palette.dark: appearance.colors.muted
    hoverEnabled: true
    HoverHandler { cursorShape: appearance.pointerCursors && control.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
    background: Rectangle {
        implicitWidth: control.chip ? 100 : 140
        implicitHeight: control.chip ? Math.max(30, appearance.uiFont.pixelSize + 17) : Math.max(40, appearance.uiFont.pixelSize + 27)
        radius: control.chip ? height / 2 : 8
        color: !control.enabled ? appearance.colors.disabled : control.down ? appearance.colors.pressed : control.chip ? (control.hovered ? appearance.colors.hover : "transparent") : appearance.colors.surfaceAlt
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? appearance.colors.accent : control.hovered && !control.chip ? appearance.colors.accent : appearance.colors.border
    }
    // The list opens as a raised surface under the control. It is modal so a
    // wheel or click outside cannot scroll the host view away from the control
    // while the list stays open; the outside press only closes the list.
    popup: Popup {
        id: list
        y: control.height + 4
        width: Math.max(control.width, 120)
        implicitHeight: Math.min(view.contentHeight + topPadding + bottomPadding, 360)
        padding: 6
        modal: true; dim: false
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        contentItem: ListView {
            id: view
            clip: true
            implicitHeight: contentHeight
            model: list.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            highlightMoveDuration: 0
            boundsBehavior: Flickable.StopAtBounds
            ScrollIndicator.vertical: ScrollIndicator { }
        }
        background: Rectangle {
            radius: 10
            color: appearance.colors.surfaceRaised
            border.color: appearance.colors.border
        }
    }
    // The built-in TextField display is literal; dropdown delegates also need
    // an explicit format for provider model, effort and session labels.
    delegate: ItemDelegate {
        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
        id: entry
        required property var model
        required property int index
        readonly property bool emphasized: highlighted || down || visualFocus
        readonly property bool current: control.currentIndex === index
        width: ListView.view.width
        height: Math.max(34, appearance.uiFont.pixelSize + 20)
        leftPadding: 12; rightPadding: 12
        text: model[control.textRole]
        Accessible.name: text
        palette.base: control.palette.base
        palette.text: control.palette.text
        palette.highlight: control.palette.highlight
        palette.highlightedText: control.palette.highlightedText
        font.weight: current ? Font.DemiBold : Font.Normal
        highlighted: control.highlightedIndex === index
        hoverEnabled: control.hoverEnabled
        HoverHandler { cursorShape: appearance.pointerCursors && entry.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
        contentItem: Text {
            text: entry.text
            textFormat: Text.PlainText
            font: entry.font
            color: entry.emphasized ? entry.palette.highlightedText : appearance.colors.text
            horizontalAlignment: entry.mirrored ? Text.AlignRight : Text.AlignLeft
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            implicitWidth: 100
            implicitHeight: 34
            radius: 7
            // Opaque rows: contrast is measured against the row itself, not the list behind it.
            color: entry.down ? Qt.darker(entry.palette.highlight, 1.08)
                              : entry.emphasized ? entry.palette.highlight : appearance.colors.surfaceRaised
            border.width: entry.visualFocus ? 2 : 0
            border.color: entry.palette.highlightedText
        }
    }
}
