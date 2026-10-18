import QtQuick

Item {
    id: host
    objectName: "kiraPanelHost"

    // Main places this item over the available workspace, below its header.
    // Panels live in the window overlay; this item only owns their geometry.
    property var activePanel: null
    property real minimumWorkWidth: 480
    readonly property real normalPanelWidth: 440 + Math.max(0, appearance.uiFont.pixelSize - 13) * 12
    readonly property bool canAffix: width >= minimumWorkWidth + normalPanelWidth
    readonly property real reservedWidth: activePanel && activePanel.visible && !activePanel.expanded && canAffix
                                                ? Math.min(normalPanelWidth, width) : 0
    signal panelPresented()

    function widthFor(panel) {
        return Math.max(0, Math.min(width, panel.expanded ? 880 + Math.max(0, appearance.uiFont.pixelSize - 13) * 20 : normalPanelWidth))
    }

    function present(panel) {
        if (activePanel === panel) return
        let previous = activePanel
        activePanel = panel
        if (previous && previous.visible) previous.close()
        panelPresented()
    }

    function dismiss(panel) {
        if (activePanel === panel) activePanel = null
    }

    function closeCurrent() {
        if (activePanel) activePanel.close()
    }
}
