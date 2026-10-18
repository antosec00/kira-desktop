import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs

TextArea {
    font.family: appearance.contentFont.family; font.styleName: appearance.contentFont.styleName; font.weight: appearance.contentFont.weight; font.italic: appearance.contentFont.italic; font.pixelSize: appearance.contentFont.pixelSize
    id: root
    property bool attachmentReceiver: true
    property bool imageAttachments: false
    property string attachmentContext: ""
    property bool attachmentBusy: false
    property string attachmentError: ""
    property bool attachmentContextError: false
    readonly property string attachmentContextErrorText: qsTr("La conversazione è cambiata: scegli di nuovo l'allegato.")
    onAttachmentContextErrorTextChanged: if (attachmentContextError) attachmentError = attachmentContextErrorText
    onAttachmentErrorChanged: if (attachmentError !== attachmentContextErrorText) attachmentContextError = false
    readOnly: attachmentBusy
    textFormat: TextEdit.PlainText
    wrapMode: TextEdit.Wrap
    selectByMouse: true
    color: appearance.colors.text
    selectionColor: appearance.colors.selection
    selectedTextColor: appearance.colors.selectedText
    placeholderTextColor: appearance.colors.muted

    // The native MIME bridge calls this only for the unchanged receiving draft.
    // Use editing operations instead of replacing the whole text, retaining undo.
    function insertAttachment(value) {
        if (!value.length) return
        var start = selectionStart
        var end = selectionEnd
        var before = text.slice(0, start)
        var after = text.slice(end)
        var prefix = before.length && !/\s$/.test(before) ? "\n" : ""
        var suffix = after.length && !/^\s/.test(after) ? "\n" : ""
        forceActiveFocus()
        remove(start, end)
        insert(start, prefix + value + suffix)
        cursorPosition = start + prefix.length + value.length + suffix.length
    }

    function insertPlainText(value) {
        if (!value.length) return
        var start = selectionStart
        var end = selectionEnd
        forceActiveFocus()
        remove(start, end)
        insert(start, value)
        cursorPosition = start + value.length
    }

    function chooseAttachments() {
        if (!enabled || readOnly) return
        forceActiveFocus()
        attachmentPicker.openingContext = attachmentContext
        attachmentPicker.open()
    }
    FileDialog {
        id: attachmentPicker
        objectName: root.objectName + "AttachmentPicker"
        property string openingContext: ""
        title: qsTr("Allega file al messaggio")
        fileMode: FileDialog.OpenFiles
        onAccepted: {
            if (openingContext !== root.attachmentContext) {
                root.attachmentContextError = true
                root.attachmentError = root.attachmentContextErrorText
                return
            }
            attachmentInputs.importUrls(root, selectedFiles)
        }
    }

    Menu {
        font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
        id: editMenu
        objectName: root.objectName + "AttachmentMenu"
        MenuItem { HoverHandler { cursorShape: appearance.pointerCursors && parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }  font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; text: qsTr("Taglia"); enabled: !root.readOnly && root.selectedText.length > 0; onTriggered: root.cut() }
        MenuItem { HoverHandler { cursorShape: appearance.pointerCursors && parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }  font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; text: qsTr("Copia"); enabled: root.selectedText.length > 0; onTriggered: root.copy() }
        MenuItem { HoverHandler { cursorShape: appearance.pointerCursors && parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
            font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
            objectName: root.objectName + "AttachmentPaste"
            text: qsTr("Incolla"); enabled: !root.readOnly && root.enabled
            onTriggered: attachmentInputs.paste(root)
        }
        MenuSeparator {}
        MenuItem { HoverHandler { cursorShape: appearance.pointerCursors && parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }  font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize; text: qsTr("Seleziona tutto"); enabled: root.length > 0; onTriggered: root.selectAll() }
    }
    TapHandler {
        acceptedButtons: Qt.RightButton
        onTapped: editMenu.popup()
    }
    Shortcut {
        sequence: "Shift+F10"
        enabled: root.activeFocus
        onActivated: editMenu.popup()
    }
}
