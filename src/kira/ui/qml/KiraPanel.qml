import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: panel
    property var panelHost: {
        let owner = ApplicationWindow.window
        return owner && typeof owner.workspacePanels !== "undefined" ? owner.workspacePanels : null
    }
    property bool expanded: false
    property real fallbackWidth: 800
    property real fallbackHeight: 780
    // Panels with a key share the window's inspector tab strip.
    property string inspectorKey: ""
    readonly property var inspectorTabs: {
        let owner = ApplicationWindow.window
        return inspectorKey.length > 0 && owner && typeof owner.inspectorTabs !== "undefined" ? owner.inspectorTabs : []
    }
    readonly property bool sidePanel: !!panelHost
    readonly property bool compactPanel: availableWidth < 560
    readonly property point hostOrigin: {
        if (!panelHost || !parent) return Qt.point(0, 0)
        // mapToItem itself does not expose geometry dependencies. The overlay
        // can move transiently while Qt applies a window resize.
        let geometry = [panelHost.x, panelHost.y, panelHost.width, panelHost.height,
                        parent.x, parent.y, parent.width, parent.height]
        return panelHost.mapToItem(parent, 0, 0)
    }

    parent: Overlay.overlay
    modal: !sidePanel
    dim: !sidePanel
    focus: true
    closePolicy: Popup.CloseOnEscape
    padding: 16
    width: panelHost ? panelHost.widthFor(panel) : Math.min(fallbackWidth, parent ? parent.width - 32 : fallbackWidth)
    height: panelHost ? panelHost.height : Math.min(fallbackHeight, parent ? parent.height - 40 : fallbackHeight)
    x: panelHost ? hostOrigin.x + panelHost.width - width : parent ? (parent.width - width) / 2 : 0
    y: panelHost ? hostOrigin.y : parent ? (parent.height - height) / 2 : 0
    font: appearance.uiFont

    // Connections allow each concrete panel to retain its existing open/close
    // handlers without replacing the common host lifecycle.
    Connections {
        target: panel
        function onAboutToShow() { if (panel.panelHost) panel.panelHost.present(panel) }
        function onClosed() { if (panel.panelHost) panel.panelHost.dismiss(panel) }
    }
    background: Rectangle {
        color: appearance.colors.surface
        radius: panel.sidePanel ? 0 : 14
        border.color: appearance.colors.border
    }
    header: Item {
        implicitHeight: panelHeader.implicitHeight + 20 + (inspectorStrip.visible ? inspectorStrip.implicitHeight + 6 : 0)
        Flow {
            id: inspectorStrip
            objectName: "inspectorTabs"
            visible: panel.sidePanel && panel.inspectorTabs.length > 0
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.leftMargin: 10; anchors.rightMargin: 10; anchors.topMargin: 8
            spacing: 2
            Repeater {
                model: panel.inspectorTabs
                delegate: KButton {
                    required property var modelData
                    objectName: "inspectorTab_" + modelData.key
                    iconName: modelData.icon; subtle: true; quietSelection: true
                    implicitWidth: 34; implicitHeight: 30
                    checked: modelData.key === panel.inspectorKey
                    toolTipText: modelData.label
                    Accessible.name: modelData.label
                    onClicked: { let owner = ApplicationWindow.window; if (owner && modelData.key !== panel.inspectorKey) owner.openInspector(modelData.key) }
                }
            }
        }
        RowLayout {
            id: panelHeader
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.leftMargin: 16; anchors.rightMargin: 10; anchors.topMargin: 10 + (inspectorStrip.visible ? inspectorStrip.implicitHeight + 6 : 0)
            spacing: 8
            Label {
                text: panel.title
                font.family: appearance.uiFont.family
                font.pixelSize: appearance.uiFont.pixelSize
                font.italic: appearance.uiFont.italic
                font.weight: Math.max(Font.DemiBold, appearance.uiFont.weight)
                color: appearance.colors.text; textFormat: Text.PlainText
                wrapMode: Text.Wrap
                Layout.fillWidth: true; Layout.minimumWidth: 0
            }
            KButton {
                objectName: panel.objectName + "Expand"
                visible: panel.sidePanel
                iconName: panel.expanded ? "chevron-right" : "external"
                subtle: true; implicitWidth: 34; implicitHeight: 34
                toolTipText: panel.expanded ? qsTr("Affianca alla chat") : qsTr("Espandi pannello")
                Accessible.name: toolTipText
                onClicked: panel.expanded = !panel.expanded
            }
            KButton {
                objectName: panel.objectName + "Close"
                iconName: "close"; subtle: true; implicitWidth: 34; implicitHeight: 34
                toolTipText: qsTr("Chiudi pannello")
                Accessible.name: toolTipText
                onClicked: panel.close()
            }
        }
        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: appearance.colors.border; opacity: 0.6 }
    }
}
