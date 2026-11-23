import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Aids for reading a long list of models: search, kind, order and maker.
// It only states choices: the list itself is filtered and ordered by the workspace.
ColumnLayout {
    id: bar
    // The choices valid for the list being shown and the ones in force
    // (workspace.modelView, or workspace.modelViewFor(rows) for another provider's list).
    property var view: ({})
    property bool compact: false
    // Distinguishes the controls of the two places this bar appears in.
    property string prefix: ""
    readonly property int chipHeight: (compact ? 26 : 30) + Math.max(0, appearance.uiFont.pixelSize - 13)
    readonly property int chipFont: Math.max(10, appearance.uiFont.pixelSize - 1)
    readonly property bool filtered: view.kind !== "all" || String(view.group || "").length > 0 || String(view.query || "").length > 0
    // Enter in the search field: the host may open its list of results.
    signal searchAccepted()
    visible: view.useful === true
    spacing: compact ? 6 : 8

    KFormat { id: kformat }

    Flow {
        Layout.fillWidth: true; spacing: 6
        TextField {
            id: search
            objectName: bar.prefix + "ModelSearch"
            width: bar.compact ? 170 : 220; height: bar.chipHeight
            topPadding: 0; bottomPadding: 0; leftPadding: 12; rightPadding: 12
            verticalAlignment: TextInput.AlignVCenter
            font.family: appearance.uiFont.family; font.pixelSize: bar.chipFont
            color: appearance.colors.text
            placeholderText: qsTr("Cerca un modello…")
            placeholderTextColor: appearance.colors.muted
            selectByMouse: true; maximumLength: 80
            Accessible.name: qsTr("Cerca un modello")
            background: Rectangle {
                radius: height / 2
                color: appearance.colors.surfaceAlt
                border.width: search.activeFocus ? 2 : 1
                border.color: search.activeFocus ? appearance.colors.accent : appearance.colors.border
            }
            Component.onCompleted: text = String(bar.view.query || "")
            onTextEdited: workspace.setModelQuery(text)
            onAccepted: bar.searchAccepted()
            // The other bar, or a reset, changed the words: follow unless the user is typing here.
            Connections {
                target: workspace
                function onModelViewChanged() {
                    const words = String(bar.view.query || "")
                    if (!search.activeFocus && search.text !== words) search.text = words
                }
            }
        }
        Repeater {
            model: bar.view.kinds || []
            delegate: KButton {
                required property var modelData
                objectName: bar.prefix + "ModelKind_" + modelData.id
                implicitHeight: bar.chipHeight; cornerRadius: height / 2; font.pixelSize: bar.chipFont
                text: kformat.modelKindLabel(modelData.id) + " · " + modelData.count
                checked: bar.view.kind === modelData.id
                onClicked: workspace.setModelKind(modelData.id)
                Accessible.name: text
            }
        }
        Text {
            visible: (bar.view.sorts || []).length > 0
            text: qsTr("Ordina:")
            textFormat: Text.PlainText
            color: appearance.colors.muted
            font.family: appearance.uiFont.family; font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 2)
            height: bar.chipHeight; verticalAlignment: Text.AlignVCenter; leftPadding: 6
        }
        Repeater {
            model: bar.view.sorts || []
            delegate: KButton {
                required property string modelData
                objectName: bar.prefix + "ModelSort_" + modelData
                implicitHeight: bar.chipHeight; cornerRadius: height / 2; font.pixelSize: bar.chipFont
                text: kformat.modelSortLabel(modelData)
                checked: bar.view.sort === modelData
                onClicked: workspace.setModelSort(modelData)
                Accessible.name: qsTr("Ordina: %1").arg(text)
                toolTipText: modelData === "capability" ? qsTr("Dal punteggio più alto nei test indipendenti di capacità; i modelli senza punteggio sono in fondo.")
                           : modelData === "recommended" ? qsTr("L'ordine proposto dal provider, casa per casa.")
                           : qsTr("Per il testo conta il costo di tre parti lette e una scritta; per le immagini il costo di un'immagine.")
            }
        }
        KButton {
            objectName: bar.prefix + "ModelReset"
            visible: bar.filtered
            implicitHeight: bar.chipHeight; cornerRadius: height / 2; font.pixelSize: bar.chipFont
            subtle: true
            text: qsTr("Mostra tutti")
            onClicked: workspace.resetModelView()
            Accessible.name: qsTr("Togli i filtri dei modelli")
        }
    }
    Flow {
        visible: (bar.view.groups || []).length > 0
        Layout.fillWidth: true; spacing: 6
        KButton {
            objectName: bar.prefix + "ModelGroup_all"
            implicitHeight: bar.chipHeight; cornerRadius: height / 2; font.pixelSize: bar.chipFont
            text: qsTr("Tutte le case")
            checked: String(bar.view.group || "").length === 0
            onClicked: workspace.setModelGroup("")
            Accessible.name: text
        }
        Repeater {
            model: bar.view.groups || []
            delegate: KButton {
                required property var modelData
                required property int index
                objectName: bar.prefix + "ModelGroup_" + index
                implicitHeight: bar.chipHeight; cornerRadius: height / 2; font.pixelSize: bar.chipFont
                text: kformat.modelGroupLabel(modelData.id) + " · " + modelData.count
                checked: bar.view.group === modelData.id
                onClicked: workspace.setModelGroup(checked ? "" : modelData.id)
                Accessible.name: text
            }
        }
    }
}
