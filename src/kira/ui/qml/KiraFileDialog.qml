import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Kira's own chooser for a folder, files to open or a file to save. It keeps
// the app's look and runs every disk operation through fileBrowser.
Dialog {
    id: picker
    // "folder", "open" (one or more files) or "save".
    property string mode: "folder"
    // The last confirmed folder is remembered per purpose.
    property string purpose: "general"
    property string subtitle: ""
    property list<string> nameFilters: []
    property string defaultSuffix: ""
    property string currentFolder: ""
    property string suggestedName: ""
    property string acceptText: mode === "folder" ? qsTr("Usa questa cartella") : mode === "save" ? qsTr("Salva") : qsTr("Apri")
    property url selectedFolder
    property string selectedPath: ""
    property url selectedFile
    property var selectedFiles: []
    property int selectedNameFilter: 0
    property string saveText: ""

    // View state of one opening.
    property var selection: []
    property int anchorIndex: -1
    property string problem: ""
    property string renamingPath: ""
    property bool creatingFolder: false
    property bool confirmOverwrite: false
    property var saveTargetInfo: ({})
    property bool showDetails: true
    // A narrow window keeps the list and the places; details need room.
    readonly property bool detailsShown: showDetails && width >= 940
    property string typeAhead: ""
    readonly property bool multiple: mode === "open"
    // Theme colors arrive as text; color properties expose their channels.
    readonly property color accent: appearance.colors.accent
    readonly property color softAccent: Qt.rgba(accent.r, accent.g, accent.b, 0.18)
    readonly property color pathFill: appearance.colors.surfaceAlt
    // Only the open chooser shows the shared listing.
    readonly property var entries: visible ? fileBrowser.entries : []
    property bool everOpened: false

    objectName: "kiraFileDialog"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(1100, (parent ? parent.width : 1100) - 40)
    height: Math.min(720, (parent ? parent.height : 720) - 40)
    modal: true
    focus: true
    padding: 0
    closePolicy: Popup.CloseOnEscape
    standardButtons: Dialog.NoButton
    font: appearance.uiFont
    header: Item { visible: false; implicitHeight: 0 }
    footer: Item { visible: false; implicitHeight: 0 }
    background: Rectangle { color: appearance.colors.surface; radius: 16; border.color: appearance.colors.border }
    Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, 0.45) }

    onAboutToShow: {
        everOpened = true
        selection = []; anchorIndex = -1; problem = ""; renamingPath = ""
        creatingFolder = false; confirmOverwrite = false; typeAhead = ""
        selectedPath = ""; selectedFiles = []; saveText = suggestedName
        fileBrowser.begin(mode, purpose, currentFolder)
        fileBrowser.setPatterns(patternsAt(selectedNameFilter))
    }

    // Helpers shared by the view.

    function entry(index) { return index >= 0 && index < entries.length ? entries[index] : null }
    function indexOfPath(path) {
        for (let i = 0; i < entries.length; ++i) if (entries[i].path === path) return i
        return -1
    }
    function isSelected(path) { return selection.indexOf(path) >= 0 }
    function selectedRows() { return entries.filter(function(row) { return selection.indexOf(row.path) >= 0 }) }
    function patternsAt(index) {
        const label = String(nameFilters.length > index && index >= 0 ? nameFilters[index] : "")
        const match = /\(([^)]*)\)/.exec(label)
        return match ? match[1].split(/\s+/).filter(function(item) { return item.length > 0 }) : []
    }
    function suffixAt(index) {
        const patterns = patternsAt(index)
        if (patterns.length === 0) return defaultSuffix
        const match = /^\*\.([A-Za-z0-9]+)$/.exec(patterns[0])
        return match ? match[1] : ""
    }
    function iconFor(row) {
        if (!row) return "file"
        switch (row.kind) {
        case "folder": return "folder"
        case "image": return "image"
        case "code": return "code"
        case "archive": return "archive"
        case "audio": return "audio"
        case "video": return "video"
        case "document": return "document"
        }
        return "file"
    }
    function kindLabel(kind) {
        switch (kind) {
        case "folder": return qsTr("Cartella")
        case "image": return qsTr("Immagine")
        case "code": return qsTr("Codice sorgente")
        case "text": return qsTr("Testo")
        case "archive": return qsTr("Archivio")
        case "audio": return qsTr("Audio")
        case "video": return qsTr("Video")
        case "document": return qsTr("Documento")
        }
        return qsTr("File")
    }
    function sizeText(bytes) {
        if (bytes === undefined || bytes < 0) return ""
        const locale = Qt.locale(localization.language)
        if (bytes < 1024) return qsTr("%1 B").arg(bytes)
        const units = ["KB", "MB", "GB", "TB"]
        let value = bytes / 1024, unit = 0
        while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1 }
        return Number(value).toLocaleString(locale, "f", value < 10 ? 1 : 0) + " " + units[unit]
    }
    function timeText(milliseconds) {
        if (!milliseconds) return ""
        return new Date(milliseconds).toLocaleString(Qt.locale(localization.language), "d MMM yyyy, HH:mm")
    }

    function show(problemText) { problem = problemText || "" }
    function go(path) { show(fileBrowser.navigate(path)) }
    function selectOnly(index) {
        const row = entry(index)
        selection = row ? [row.path] : []
        anchorIndex = index
        if (row && mode === "save" && !row.isDir) saveText = row.name
    }
    function toggle(index) {
        const row = entry(index)
        if (!row) return
        const next = selection.slice()
        const at = next.indexOf(row.path)
        if (at >= 0) next.splice(at, 1); else next.push(row.path)
        selection = next
        anchorIndex = index
    }
    function extendTo(index) {
        if (anchorIndex < 0) { selectOnly(index); return }
        const low = Math.min(anchorIndex, index), high = Math.max(anchorIndex, index)
        const next = []
        for (let i = low; i <= high; ++i) next.push(entries[i].path)
        selection = next
    }
    function clickRow(index, modifiers) {
        if (multiple && (modifiers & Qt.ControlModifier)) toggle(index)
        else if (multiple && (modifiers & Qt.ShiftModifier)) extendTo(index)
        else selectOnly(index)
    }
    // Enter or double click: folders open, files are chosen.
    function activate(index) {
        const row = entry(index)
        if (!row) return
        if (row.isDir) { go(row.path); return }
        if (mode === "open") { selectOnly(index); acceptChoice() }
        else if (mode === "save") { saveText = row.name; acceptChoice() }
    }
    function folderChoice() {
        const rows = selectedRows()
        return rows.length === 1 && rows[0].isDir ? rows[0].path : fileBrowser.path
    }
    function canAccept() {
        if (fileBrowser.loading && mode !== "folder") return false
        if (mode === "folder") return fileBrowser.path.length > 0 && fileBrowser.error.length === 0
        if (mode === "open") return selectedRows().some(function(row) { return !row.isDir })
        return saveText.trim().length > 0 && fileBrowser.error.length === 0
    }
    function acceptChoice() {
        if (!canAccept()) return
        if (mode === "folder") {
            selectedPath = folderChoice()
            selectedFolder = fileBrowser.urlFor(selectedPath)
            fileBrowser.remember(selectedPath)
            accept()
        } else if (mode === "open") {
            const files = selectedRows().filter(function(row) { return !row.isDir }).map(function(row) { return row.path })
            selectedFiles = fileBrowser.urlsFor(files)
            selectedFile = selectedFiles[0]
            selectedPath = files[0]
            fileBrowser.remember(fileBrowser.path)
            accept()
        } else {
            const target = fileBrowser.saveTarget(saveText.trim(), suffixAt(selectedNameFilter))
            if (target.error) { show(target.error); return }
            if (target.exists && !confirmOverwrite) { saveTargetInfo = target; confirmOverwrite = true; return }
            selectedFile = target.url
            selectedPath = target.path
            fileBrowser.remember(fileBrowser.path)
            confirmOverwrite = false
            accept()
        }
    }
    function startRename() {
        const rows = selectedRows()
        if (rows.length === 1) { renamingPath = rows[0].path; problem = "" }
    }
    function finishRename(path, name) {
        const error = fileBrowser.rename(path, name)
        show(error)
        if (!error) renamingPath = ""
    }
    function finishCreate(name) {
        const error = fileBrowser.createFolder(name)
        show(error)
        if (!error) creatingFolder = false
    }
    function copyPaths() {
        const rows = selectedRows()
        fileBrowser.copyText(rows.length ? rows.map(function(row) { return row.path }).join("\n") : fileBrowser.path)
    }

    // After every listing, select what the user came from or just created.
    // It runs once the listing bindings have settled.
    function syncSelection() {
        if (!visible) return
        const kept = selection.filter(function(path) { return indexOfPath(path) >= 0 })
        if (kept.length !== selection.length) selection = kept
        if (fileBrowser.loading) return
        if (fileBrowser.selectName.length > 0 && selection.length === 0) {
            for (let i = 0; i < entries.length; ++i) {
                if (entries[i].name === fileBrowser.selectName) {
                    selectOnly(i)
                    if (contentItem.item) contentItem.item.reveal(i)
                    break
                }
            }
        }
        if (selection.length === 0) previewDelay.restart()
    }
    Connections {
        target: fileBrowser
        function onChanged() { Qt.callLater(picker.syncSelection) }
    }
    Timer { id: typeAheadReset; interval: 900; onTriggered: picker.typeAhead = "" }
    Timer {
        id: previewDelay; interval: 140
        onTriggered: {
            const rows = picker.selectedRows()
            fileBrowser.requestPreview(rows.length === 1 ? rows[0].path : fileBrowser.path)
        }
    }
    onSelectionChanged: if (visible) previewDelay.restart()

    contentItem: Loader {
        // Built on the first opening and kept: destroying it on every close
        // raced with the destruction of the QML engine.
        active: picker.everOpened
        sourceComponent: chooser
    }

    Component {
        id: chooser
        Item {
            id: body
            readonly property real rowHeight: Math.max(34, appearance.uiFont.pixelSize + 20)
            function reveal(index) { list.currentIndex = index; list.positionViewAtIndex(index, ListView.Contain) }
            Component.onCompleted: list.forceActiveFocus()
            Connections { target: picker; function onOpened() { pathBar.editing = false; search.text = ""; list.forceActiveFocus() } }

            DropArea {
                anchors.fill: parent
                onEntered: function(drag) { drag.accepted = drag.hasUrls }
                onDropped: function(drop) { if (drop.hasUrls && drop.urls.length > 0) picker.go(drop.urls[0].toString()) }
            }

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                // Title and purpose.
                RowLayout {
                    Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 12; Layout.topMargin: 14; Layout.bottomMargin: 8
                    spacing: 12
                    Rectangle {
                        width: 36; height: 36; radius: 10; color: appearance.colors.accentSoft
                        KIcon { anchors.centerIn: parent; width: 20; height: 20; name: picker.mode === "save" ? "export" : picker.mode === "open" ? "attach" : "folder" }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 1
                        Text { objectName: "fileDialogTitle"; text: picker.title; color: appearance.colors.text; font.family: appearance.uiFont.family; font.pixelSize: appearance.uiFont.pixelSize + 3; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true; textFormat: Text.PlainText }
                        Text {
                            text: picker.subtitle.length ? picker.subtitle : picker.mode === "folder" ? qsTr("Apri una cartella e premi «Usa questa cartella», oppure selezionane una nell'elenco.")
                                  : picker.mode === "open" ? qsTr("Seleziona uno o più file: Ctrl e Shift estendono la selezione.") : qsTr("Scegli la cartella, poi il nome del file.")
                            color: appearance.colors.muted; font: appearance.uiFont; elide: Text.ElideRight; Layout.fillWidth: true; textFormat: Text.PlainText
                        }
                    }
                    KButton { objectName: "fileDialogClose"; iconName: "close"; subtle: true; Accessible.name: qsTr("Chiudi"); toolTipText: qsTr("Chiudi · Esc"); onClicked: picker.reject() }
                }

                // Navigation, path and search.
                RowLayout {
                    Layout.fillWidth: true; Layout.leftMargin: 16; Layout.rightMargin: 16; Layout.bottomMargin: 10
                    spacing: 6
                    KButton { objectName: "fileDialogBack"; iconName: "back"; subtle: true; enabled: fileBrowser.canGoBack; onClicked: fileBrowser.goBack(); Accessible.name: qsTr("Indietro"); toolTipText: qsTr("Indietro · Alt+←") }
                    KButton { objectName: "fileDialogForward"; iconName: "forward"; subtle: true; enabled: fileBrowser.canGoForward; onClicked: fileBrowser.goForward(); Accessible.name: qsTr("Avanti"); toolTipText: qsTr("Avanti · Alt+→") }
                    KButton { objectName: "fileDialogUp"; iconName: "up"; subtle: true; enabled: fileBrowser.canGoUp; onClicked: fileBrowser.goUp(); Accessible.name: qsTr("Cartella superiore"); toolTipText: qsTr("Cartella superiore · Backspace") }
                    Rectangle {
                        id: pathBar
                        property bool editing: false
                        Layout.fillWidth: true; implicitHeight: 38; radius: 10
                        color: appearance.colors.surfaceAlt
                        border.color: pathField.activeFocus ? appearance.colors.accent : appearance.colors.border
                        ListView {
                            id: crumbs; objectName: "fileDialogCrumbs"
                            visible: !pathBar.editing
                            anchors.fill: parent; anchors.leftMargin: 4; anchors.rightMargin: 40
                            orientation: ListView.Horizontal; clip: true; spacing: 0
                            interactive: contentWidth > width
                            model: fileBrowser.crumbs
                            // The deepest folder stays visible as the crumbs grow.
                            onContentWidthChanged: positionViewAtEnd()
                            onWidthChanged: positionViewAtEnd()
                            delegate: Row {
                                required property var modelData
                                required property int index
                                height: crumbs.height
                                KIcon { visible: index > 1; name: "chevron-right"; ink: appearance.colors.muted; width: 12; height: 12; anchors.verticalCenter: parent.verticalCenter }
                                KButton {
                                    anchors.verticalCenter: parent.verticalCenter
                                    subtle: true; implicitHeight: 30; cornerRadius: 8
                                    text: modelData.label
                                    checked: index === crumbs.count - 1; quietSelection: true
                                    onClicked: picker.go(modelData.path)
                                    Accessible.name: qsTr("Vai a %1").arg(modelData.path)
                                }
                            }
                        }
                        // Long paths keep their end visible and fade out at the start.
                        Rectangle {
                            visible: !pathBar.editing && crumbs.contentX > 1
                            anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                            anchors.margins: 1; width: 34; radius: 10
                            gradient: Gradient {
                                orientation: Gradient.Horizontal
                                GradientStop { position: 0.0; color: picker.pathFill }
                                GradientStop { position: 1.0; color: Qt.rgba(picker.pathFill.r, picker.pathFill.g, picker.pathFill.b, 0) }
                            }
                        }
                        TextField {
                            id: pathField; objectName: "fileDialogPathField"
                            visible: pathBar.editing
                            anchors.fill: parent; anchors.rightMargin: 36
                            background: null; font: appearance.uiFont; color: appearance.colors.text; selectByMouse: true
                            placeholderText: qsTr("Percorso, anche C:\\… o \\\\wsl.localhost\\…")
                            function submit() { picker.go(text); if (!picker.problem.length) { pathBar.editing = false; list.forceActiveFocus() } }
                            Keys.onReturnPressed: function(event) { submit(); event.accepted = true }
                            Keys.onEnterPressed: function(event) { submit(); event.accepted = true }
                            Keys.onEscapePressed: function(event) { pathBar.editing = false; list.forceActiveFocus(); event.accepted = true }
                            onActiveFocusChanged: if (!activeFocus) pathBar.editing = false
                        }
                        KButton {
                            objectName: "fileDialogEditPath"
                            anchors.right: parent.right; anchors.rightMargin: 3; anchors.verticalCenter: parent.verticalCenter
                            implicitWidth: 32; implicitHeight: 32; cornerRadius: 8
                            iconName: pathBar.editing ? "close" : "rename"; subtle: true
                            Accessible.name: pathBar.editing ? qsTr("Annulla la modifica del percorso") : qsTr("Scrivi un percorso")
                            toolTipText: pathBar.editing ? qsTr("Annulla · Esc") : qsTr("Scrivi o incolla un percorso · Ctrl+L")
                            onClicked: body.editPath(!pathBar.editing)
                        }
                    }
                    KButton { objectName: "fileDialogRefresh"; iconName: "refresh"; subtle: true; onClicked: fileBrowser.refresh(); Accessible.name: qsTr("Aggiorna"); toolTipText: qsTr("Aggiorna · F5") }
                    TextField {
                        id: search; objectName: "fileDialogSearch"
                        Layout.preferredWidth: 220; implicitHeight: 38
                        font: appearance.uiFont; selectByMouse: true
                        placeholderText: qsTr("Cerca in questa cartella")
                        Accessible.name: qsTr("Cerca in questa cartella")
                        leftPadding: 34
                        KIcon { name: "search"; ink: appearance.colors.muted; width: 16; height: 16; anchors.left: parent.left; anchors.leftMargin: 11; anchors.verticalCenter: parent.verticalCenter }
                        onTextChanged: fileBrowser.setFilter(text)
                        Keys.onDownPressed: function(event) { list.forceActiveFocus(); if (picker.entries.length) { picker.selectOnly(0); body.reveal(0) } event.accepted = true }
                        Keys.onEscapePressed: function(event) { if (text.length) text = ""; else picker.reject(); event.accepted = true }
                    }
                    KButton {
                        objectName: "fileDialogNewFolder"; iconName: "folder-plus"; subtle: true
                        enabled: fileBrowser.writable && !fileBrowser.loading
                        onClicked: { picker.creatingFolder = true; picker.problem = "" }
                        Accessible.name: qsTr("Nuova cartella"); toolTipText: qsTr("Nuova cartella · Ctrl+Shift+N")
                    }
                    KButton {
                        objectName: "fileDialogDetailsToggle"; iconName: "details"; subtle: true; checked: picker.showDetails; quietSelection: true
                        visible: picker.width >= 940
                        onClicked: picker.showDetails = !picker.showDetails
                        Accessible.name: qsTr("Dettagli e anteprima"); toolTipText: qsTr("Mostra o nascondi i dettagli")
                    }
                    KButton { id: viewMenuButton; objectName: "fileDialogMenu"; iconName: "more"; subtle: true; onClicked: viewMenu.popup(viewMenuButton, 0, viewMenuButton.height); Accessible.name: qsTr("Altre azioni"); toolTipText: qsTr("Ordina, file nascosti e altre azioni") }
                }

                // Problems stay visible until the next action.
                Rectangle {
                    objectName: "fileDialogProblem"
                    visible: picker.problem.length > 0
                    Layout.fillWidth: true; Layout.leftMargin: 16; Layout.rightMargin: 16; Layout.bottomMargin: 8
                    implicitHeight: problemText.implicitHeight + 16; radius: 8
                    color: appearance.colors.dangerBackground
                    border.color: appearance.colors.danger
                    Text { id: problemText; anchors.fill: parent; anchors.margins: 8; anchors.rightMargin: 34; text: picker.problem; color: appearance.colors.text; wrapMode: Text.Wrap; font: appearance.uiFont; textFormat: Text.PlainText }
                    KButton { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; iconName: "close"; subtle: true; implicitWidth: 30; implicitHeight: 30; onClicked: picker.problem = ""; Accessible.name: qsTr("Chiudi l'avviso") }
                }

                Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: appearance.colors.border }

                RowLayout {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    spacing: 0

                    // Places.
                    Rectangle {
                        Layout.fillHeight: true; Layout.preferredWidth: Math.min(230, picker.width * 0.22)
                        color: appearance.colors.surfaceAlt
                        ListView {
                            id: places; objectName: "fileDialogPlaces"
                            anchors.fill: parent; anchors.margins: 8
                            clip: true; spacing: 2
                            model: fileBrowser.places
                            ScrollBar.vertical: KScrollBar {}
                            delegate: Column {
                                id: place
                                required property var modelData
                                required property int index
                                width: places.width
                                Text {
                                    visible: place.index === 0 || places.model[place.index - 1].group !== place.modelData.group
                                    width: parent.width; topPadding: place.index === 0 ? 2 : 12; bottomPadding: 4; leftPadding: 10
                                    text: String(place.modelData.group).toUpperCase(); color: appearance.colors.muted
                                    font.family: appearance.uiFont.family; font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 3); font.weight: Font.DemiBold; font.letterSpacing: 0.8
                                    elide: Text.ElideRight; textFormat: Text.PlainText
                                }
                                KButton {
                                    width: parent.width; implicitHeight: 34; cornerRadius: 8
                                    leftAligned: true; subtle: true; quietSelection: true
                                    iconName: place.modelData.icon; text: place.modelData.label
                                    checked: fileBrowser.path === place.modelData.path
                                    toolTipText: place.modelData.path
                                    onClicked: picker.go(place.modelData.path)
                                }
                            }
                        }
                    }
                    Rectangle { Layout.fillHeight: true; implicitWidth: 1; color: appearance.colors.border }

                    // Entries.
                    ColumnLayout {
                        Layout.fillWidth: true; Layout.fillHeight: true
                        spacing: 0
                        RowLayout {
                            id: columns
                            Layout.fillWidth: true; Layout.leftMargin: 14; Layout.rightMargin: 22; Layout.topMargin: 6
                            spacing: 10
                            readonly property real dateWidth: 150
                            readonly property real sizeWidth: 86
                            Item { implicitWidth: 18 }
                            SortHeader { objectName: "fileDialogSortName"; key: "name"; text: qsTr("Nome"); Layout.fillWidth: true }
                            SortHeader { objectName: "fileDialogSortModified"; key: "modified"; text: qsTr("Modificato"); Layout.preferredWidth: columns.dateWidth }
                            SortHeader { objectName: "fileDialogSortSize"; key: "size"; text: qsTr("Dimensione"); Layout.preferredWidth: columns.sizeWidth; alignRight: true }
                        }

                        Item {
                            Layout.fillWidth: true; Layout.fillHeight: true
                            ListView {
                                id: list; objectName: "fileDialogEntries"
                                anchors.fill: parent; anchors.margins: 6; anchors.rightMargin: 4
                                clip: true; spacing: 1; focus: true
                                model: picker.entries
                                currentIndex: -1
                                // The keyboard cursor appears only once the user moves it.
                                onCountChanged: if (picker.selection.length === 0) currentIndex = -1
                                keyNavigationEnabled: false
                                boundsBehavior: Flickable.StopAtBounds
                                ScrollBar.vertical: KScrollBar {}
                                Accessible.name: qsTr("Contenuto della cartella")
                                header: Item {
                                    width: list.width
                                    height: picker.creatingFolder ? 40 : 0
                                    visible: picker.creatingFolder
                                    Rectangle {
                                        anchors.fill: parent; anchors.bottomMargin: 2; radius: 8
                                        color: picker.softAccent
                                        border.color: picker.accent
                                        RowLayout {
                                            anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8; spacing: 10
                                            KIcon { name: "folder-plus"; width: 18; height: 18 }
                                            TextField {
                                                id: newFolderName; objectName: "fileDialogNewFolderName"
                                                Layout.fillWidth: true; font: appearance.uiFont; selectByMouse: true
                                                text: qsTr("Nuova cartella")
                                                Accessible.name: qsTr("Nome della nuova cartella")
                                                onVisibleChanged: if (visible) { text = qsTr("Nuova cartella"); selectAll(); forceActiveFocus() }
                                                // Return stays here: the list would read it as "choose".
                                                Keys.onReturnPressed: function(event) { picker.finishCreate(text); event.accepted = true }
                                                Keys.onEnterPressed: function(event) { picker.finishCreate(text); event.accepted = true }
                                                Keys.onEscapePressed: function(event) { picker.creatingFolder = false; list.forceActiveFocus(); event.accepted = true }
                                            }
                                            KButton { text: qsTr("Crea"); primary: true; implicitHeight: 32; onClicked: picker.finishCreate(newFolderName.text) }
                                        }
                                    }
                                }
                                delegate: Rectangle {
                                    id: row
                                    required property var modelData
                                    required property int index
                                    readonly property bool selectedRow: picker.isSelected(modelData.path)
                                    readonly property bool renaming: picker.renamingPath === modelData.path
                                    width: list.width - 10
                                    height: body.rowHeight
                                    radius: 8
                                    // A soft accent keeps every column readable in both themes.
                                    color: selectedRow ? Qt.rgba(picker.accent.r, picker.accent.g, picker.accent.b, rowMouse.containsMouse ? 0.26 : 0.2)
                                           : rowMouse.containsMouse ? appearance.colors.hover : "transparent"
                                    border.width: selectedRow || (list.currentIndex === index && list.activeFocus) ? 1 : 0
                                    border.color: Qt.rgba(picker.accent.r, picker.accent.g, picker.accent.b, selectedRow ? 0.6 : 0.85)
                                    Accessible.role: Accessible.ListItem
                                    Accessible.name: modelData.name
                                    Accessible.selected: selectedRow
                                    MouseArea {
                                        id: rowMouse
                                        anchors.fill: parent; hoverEnabled: true
                                        acceptedButtons: Qt.LeftButton | Qt.RightButton
                                        onClicked: function(mouse) {
                                            list.forceActiveFocus()
                                            list.currentIndex = row.index
                                            if (mouse.button === Qt.RightButton) {
                                                if (!row.selectedRow) picker.selectOnly(row.index)
                                                entryMenu.row = row.modelData
                                                entryMenu.popup()
                                            } else {
                                                picker.clickRow(row.index, mouse.modifiers)
                                            }
                                        }
                                        onDoubleClicked: function(mouse) { if (mouse.button === Qt.LeftButton) picker.activate(row.index) }
                                    }
                                    RowLayout {
                                        anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 12
                                        spacing: 10
                                        KIcon {
                                            name: picker.iconFor(row.modelData); width: 18; height: 18
                                            ink: row.modelData.isDir ? appearance.colors.accent : appearance.colors.textSecondary
                                            opacity: row.modelData.hidden ? 0.55 : 1
                                        }
                                        Text {
                                            visible: !row.renaming
                                            Layout.fillWidth: true
                                            text: row.modelData.name; elide: Text.ElideMiddle; textFormat: Text.PlainText
                                            color: appearance.colors.text
                                            opacity: row.modelData.hidden ? 0.65 : 1
                                            font.family: appearance.uiFont.family; font.pixelSize: appearance.uiFont.pixelSize; font.italic: row.modelData.broken
                                        }
                                        KIcon { visible: row.modelData.link && !row.renaming; name: "link"; width: 13; height: 13; ink: appearance.colors.muted }
                                        TextField {
                                            visible: row.renaming
                                            objectName: row.renaming ? "fileDialogRenameField" : ""
                                            Layout.fillWidth: true; implicitHeight: 30; font: appearance.uiFont; selectByMouse: true
                                            Accessible.name: qsTr("Nuovo nome")
                                            onVisibleChanged: if (visible) {
                                                text = row.modelData.name
                                                const dot = row.modelData.isDir ? -1 : text.lastIndexOf(".")
                                                select(0, dot > 0 ? dot : text.length)
                                                forceActiveFocus()
                                            }
                                            Keys.onReturnPressed: function(event) { picker.finishRename(row.modelData.path, text); event.accepted = true }
                                            Keys.onEnterPressed: function(event) { picker.finishRename(row.modelData.path, text); event.accepted = true }
                                            Keys.onEscapePressed: function(event) { picker.renamingPath = ""; list.forceActiveFocus(); event.accepted = true }
                                        }
                                        Text {
                                            Layout.preferredWidth: columns.dateWidth
                                            text: picker.timeText(row.modelData.modified); elide: Text.ElideRight; textFormat: Text.PlainText
                                            color: appearance.colors.muted; font: appearance.uiFont
                                        }
                                        Text {
                                            Layout.preferredWidth: columns.sizeWidth; horizontalAlignment: Text.AlignRight
                                            text: row.modelData.isDir ? "" : picker.sizeText(row.modelData.size); textFormat: Text.PlainText
                                            color: appearance.colors.muted; font: appearance.uiFont
                                        }
                                    }
                                }

                                // The empty area below the rows clears the selection; the right button offers folder actions.
                                footer: MouseArea {
                                    objectName: "fileDialogEmptyArea"
                                    width: list.width
                                    height: Math.max(28, list.height - list.count * (body.rowHeight + list.spacing) - (picker.creatingFolder ? 40 : 0))
                                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                                    onClicked: function(mouse) {
                                        list.forceActiveFocus()
                                        picker.selection = []
                                        list.currentIndex = -1
                                        if (mouse.button === Qt.RightButton) { entryMenu.row = null; entryMenu.popup() }
                                    }
                                }

                                Keys.onPressed: function(event) { body.keyPressed(event) }
                            }

                            // Loading, empty and error states.
                            ColumnLayout {
                                anchors.centerIn: parent
                                width: Math.min(parent.width - 40, 380)
                                visible: fileBrowser.loading || fileBrowser.error.length > 0 || (picker.entries.length === 0 && !picker.creatingFolder)
                                spacing: 10
                                BusyIndicator { Layout.alignment: Qt.AlignHCenter; running: fileBrowser.loading; visible: fileBrowser.loading; implicitWidth: 34; implicitHeight: 34 }
                                KIcon { Layout.alignment: Qt.AlignHCenter; visible: !fileBrowser.loading; name: fileBrowser.error.length ? "lock" : "folder"; width: 34; height: 34; ink: appearance.colors.muted }
                                Text {
                                    objectName: "fileDialogState"
                                    Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap; textFormat: Text.PlainText
                                    color: appearance.colors.muted; font: appearance.uiFont
                                    text: fileBrowser.loading ? qsTr("Caricamento…") : fileBrowser.error.length ? fileBrowser.error
                                          : fileBrowser.filterText.length ? qsTr("Nessun elemento contiene «%1».").arg(fileBrowser.filterText)
                                          : fileBrowser.hiddenCount > 0 ? qsTr("Ci sono solo elementi nascosti: Ctrl+H li mostra.") : qsTr("Questa cartella è vuota.")
                                }
                                KButton { Layout.alignment: Qt.AlignHCenter; visible: fileBrowser.error.length > 0 && !fileBrowser.loading; text: qsTr("Riprova"); onClicked: fileBrowser.refresh() }
                            }
                        }

                        // Folder status.
                        Text {
                            objectName: "fileDialogStatus"
                            Layout.fillWidth: true; Layout.leftMargin: 16; Layout.rightMargin: 16; Layout.bottomMargin: 6
                            elide: Text.ElideRight; textFormat: Text.PlainText; color: appearance.colors.muted
                            font.family: appearance.uiFont.family; font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 1)
                            text: {
                                const parts = [picker.entries.length === 1 ? qsTr("1 elemento") : qsTr("%1 elementi").arg(picker.entries.length)]
                                if (fileBrowser.hiddenCount > 0) parts.push(qsTr("%1 nascosti").arg(fileBrowser.hiddenCount))
                                if (picker.selection.length > 1) parts.push(qsTr("%1 selezionati").arg(picker.selection.length))
                                if (fileBrowser.truncated) parts.push(qsTr("mostrati solo i primi 5000"))
                                if (!fileBrowser.writable && !fileBrowser.loading && fileBrowser.error.length === 0) parts.push(qsTr("sola lettura"))
                                return parts.join("  ·  ")
                            }
                        }
                    }

                    // Details and preview.
                    Rectangle { visible: picker.detailsShown; Layout.fillHeight: true; implicitWidth: 1; color: appearance.colors.border }
                    Flickable {
                        id: details; objectName: "fileDialogDetails"
                        visible: picker.detailsShown
                        Layout.fillHeight: true; Layout.preferredWidth: Math.min(280, picker.width * 0.26)
                        clip: true; contentHeight: detailsColumn.implicitHeight + 28
                        ScrollBar.vertical: KScrollBar {}
                        readonly property var info: fileBrowser.preview
                        ColumnLayout {
                            id: detailsColumn
                            x: 14; y: 14; width: details.width - 28
                            spacing: 10
                            Rectangle {
                                Layout.fillWidth: true; implicitHeight: 150; radius: 12
                                color: appearance.colors.surfaceAlt; border.color: appearance.colors.border
                                Image {
                                    id: thumbnail; objectName: "fileDialogThumbnail"
                                    anchors.fill: parent; anchors.margins: 8
                                    visible: status === Image.Ready
                                    source: details.info.imageUrl || ""
                                    asynchronous: true; cache: false
                                    fillMode: Image.PreserveAspectFit; smooth: true
                                    sourceSize.width: 520; sourceSize.height: 300
                                }
                                KIcon {
                                    anchors.centerIn: parent; width: 52; height: 52; strokeWidth: 1.2
                                    visible: thumbnail.status !== Image.Ready
                                    name: details.info.kind ? picker.iconFor({"kind": details.info.kind}) : "folder"
                                    ink: details.info.kind === "folder" || !details.info.kind ? appearance.colors.accent : appearance.colors.textSecondary
                                }
                            }
                            Text {
                                Layout.fillWidth: true; text: details.info.name || ""; wrapMode: Text.WrapAnywhere; maximumLineCount: 3; elide: Text.ElideRight
                                color: appearance.colors.text; font.family: appearance.uiFont.family; font.pixelSize: appearance.uiFont.pixelSize + 1; font.weight: Font.DemiBold; textFormat: Text.PlainText
                            }
                            Text { visible: !!details.info.error; Layout.fillWidth: true; text: details.info.error || ""; color: appearance.colors.muted; wrapMode: Text.Wrap; font: appearance.uiFont; textFormat: Text.PlainText }
                            GridLayout {
                                Layout.fillWidth: true; columns: 2; columnSpacing: 10; rowSpacing: 6
                                visible: !!details.info.path
                                DetailKey { text: qsTr("Tipo") }
                                DetailValue { text: picker.kindLabel(details.info.kind) }
                                DetailKey { visible: details.info.kind === "folder"; text: qsTr("Contenuto") }
                                DetailValue {
                                    visible: details.info.kind === "folder"
                                    text: details.info.unreadable ? qsTr("non leggibile") : (details.info.itemsTruncated ? qsTr("oltre %1 elementi").arg(details.info.items) : details.info.items === 1 ? qsTr("1 elemento") : qsTr("%1 elementi").arg(details.info.items || 0))
                                }
                                DetailKey { visible: details.info.kind !== "folder"; text: qsTr("Dimensione") }
                                DetailValue { visible: details.info.kind !== "folder"; text: picker.sizeText(details.info.size) }
                                DetailKey { text: qsTr("Modificato") }
                                DetailValue { text: picker.timeText(details.info.modified) }
                                DetailKey { text: qsTr("Permessi") }
                                DetailValue { text: details.info.permissions || ""; font.family: appearance.codeFont.family }
                                DetailKey { text: qsTr("Percorso") }
                                DetailValue { text: details.info.path || ""; font.family: appearance.codeFont.family; font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 1) }
                            }
                            RowLayout {
                                visible: !!details.info.path
                                Layout.fillWidth: true; spacing: 6
                                KButton { iconName: "copy"; text: qsTr("Copia percorso"); subtle: true; implicitHeight: 32; onClicked: fileBrowser.copyText(details.info.path) }
                                KButton { iconName: "external"; subtle: true; implicitHeight: 32; onClicked: fileBrowser.openExternally(details.info.path); Accessible.name: details.info.kind === "folder" ? qsTr("Apri nel gestore file") : qsTr("Apri con l'app predefinita"); toolTipText: Accessible.name }
                            }
                            Rectangle {
                                visible: (details.info.text || "").length > 0
                                Layout.fillWidth: true; implicitHeight: Math.min(260, previewText.implicitHeight + 16); radius: 10
                                color: appearance.colors.codeBackground; border.color: appearance.colors.border; clip: true
                                Text {
                                    id: previewText; objectName: "fileDialogTextPreview"
                                    anchors.fill: parent; anchors.margins: 8
                                    text: details.info.text || ""; textFormat: Text.PlainText; wrapMode: Text.NoWrap; elide: Text.ElideRight
                                    color: appearance.colors.codeText; font.family: appearance.codeFont.family; font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 2)
                                }
                            }
                            Text { visible: !!details.info.textTruncated; Layout.fillWidth: true; text: qsTr("Anteprima delle prime righe."); color: appearance.colors.muted; font.family: appearance.uiFont.family; font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 2); textFormat: Text.PlainText }
                        }
                    }
                }

                Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: appearance.colors.border }

                // Replacing an existing file is always confirmed.
                Rectangle {
                    objectName: "fileDialogOverwrite"
                    visible: picker.confirmOverwrite
                    Layout.fillWidth: true; implicitHeight: overwriteRow.implicitHeight + 16
                    color: appearance.colors.surfaceAlt
                    RowLayout {
                        id: overwriteRow
                        anchors.fill: parent; anchors.leftMargin: 18; anchors.rightMargin: 18
                        Text { Layout.fillWidth: true; text: qsTr("«%1» esiste già in questa cartella. Vuoi sostituirlo?").arg(picker.saveTargetInfo.name || ""); color: appearance.colors.warning; wrapMode: Text.Wrap; font: appearance.uiFont; textFormat: Text.PlainText }
                        KButton { text: qsTr("Non sostituire"); subtle: true; onClicked: picker.confirmOverwrite = false }
                        KButton { objectName: "fileDialogReplace"; text: qsTr("Sostituisci"); destructive: true; onClicked: picker.acceptChoice() }
                    }
                }

                // Choice and confirmation.
                RowLayout {
                    Layout.fillWidth: true; Layout.leftMargin: 18; Layout.rightMargin: 16; Layout.topMargin: 10; Layout.bottomMargin: 12
                    spacing: 10
                    Text {
                        objectName: "fileDialogSummary"
                        visible: picker.mode !== "save"
                        Layout.fillWidth: true; elide: Text.ElideMiddle; textFormat: Text.PlainText
                        color: appearance.colors.textSecondary; font: appearance.uiFont
                        text: {
                            if (picker.mode === "folder") return qsTr("Cartella scelta: %1").arg(picker.folderChoice())
                            const files = picker.selectedRows().filter(function(row) { return !row.isDir })
                            if (files.length === 0) return qsTr("Seleziona uno o più file.")
                            if (files.length === 1) return files[0].name + "  ·  " + picker.sizeText(files[0].size)
                            const total = files.reduce(function(sum, row) { return sum + Math.max(0, row.size) }, 0)
                            return qsTr("%1 file selezionati").arg(files.length) + "  ·  " + picker.sizeText(total)
                        }
                    }
                    Text { visible: picker.mode === "save"; text: qsTr("Nome"); color: appearance.colors.textSecondary; font: appearance.uiFont; textFormat: Text.PlainText }
                    TextField {
                        id: saveName; objectName: "fileDialogSaveName"
                        visible: picker.mode === "save"
                        Layout.fillWidth: true; font: appearance.uiFont; selectByMouse: true
                        Component.onCompleted: text = picker.saveText
                        placeholderText: qsTr("Nome del file")
                        Accessible.name: qsTr("Nome del file")
                        onTextChanged: { if (text !== picker.saveText) picker.saveText = text; picker.confirmOverwrite = false }
                        Connections { target: picker; function onSaveTextChanged() { if (saveName.text !== picker.saveText) saveName.text = picker.saveText } }
                        Keys.onReturnPressed: function(event) { picker.acceptChoice(); event.accepted = true }
                        Keys.onEnterPressed: function(event) { picker.acceptChoice(); event.accepted = true }
                    }
                    KComboBox {
                        objectName: "fileDialogFormat"
                        visible: picker.mode !== "folder" && picker.nameFilters.length > 1
                        Layout.preferredWidth: 220
                        model: picker.nameFilters
                        currentIndex: picker.selectedNameFilter
                        Accessible.name: qsTr("Tipo di file")
                        onActivated: function(index) { picker.selectedNameFilter = index; fileBrowser.setPatterns(picker.patternsAt(index)); picker.confirmOverwrite = false }
                    }
                    KButton { objectName: "fileDialogCancel"; text: qsTr("Annulla"); subtle: true; onClicked: picker.reject() }
                    KButton {
                        objectName: "fileDialogAccept"
                        primary: true; text: picker.mode === "open" && picker.selection.length > 1 ? qsTr("%1 (%2)").arg(picker.acceptText).arg(picker.selectedRows().filter(function(row) { return !row.isDir }).length) : picker.acceptText
                        enabled: picker.canAccept()
                        onClicked: picker.acceptChoice()
                    }
                }
            }

            function editPath(on) {
                pathBar.editing = on
                if (on) { pathField.text = fileBrowser.path; pathField.selectAll(); pathField.forceActiveFocus() } else list.forceActiveFocus()
            }

            function keyPressed(event) {
                const count = picker.entries.length
                const control = event.modifiers & Qt.ControlModifier
                const alt = event.modifiers & Qt.AltModifier
                const shift = event.modifiers & Qt.ShiftModifier
                const move = function(index) {
                    index = Math.max(0, Math.min(count - 1, index))
                    if (count === 0) return
                    if (shift && picker.multiple) picker.extendTo(index); else picker.selectOnly(index)
                    body.reveal(index)
                }
                const current = list.currentIndex
                const page = Math.max(1, Math.floor(list.height / 36))
                if (alt && event.key === Qt.Key_Left) fileBrowser.goBack()
                else if (alt && event.key === Qt.Key_Right) fileBrowser.goForward()
                else if ((alt && event.key === Qt.Key_Up) || event.key === Qt.Key_Backspace) fileBrowser.goUp()
                else if (event.key === Qt.Key_Down) move(current < 0 ? 0 : current + 1)
                else if (event.key === Qt.Key_Up) move(current < 0 ? 0 : current - 1)
                else if (event.key === Qt.Key_Home) move(0)
                else if (event.key === Qt.Key_End) move(count - 1)
                else if (event.key === Qt.Key_PageDown) move(current + page)
                else if (event.key === Qt.Key_PageUp) move(current - page)
                else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                    if (control) picker.acceptChoice()
                    else if (current >= 0) picker.activate(current)
                    else picker.acceptChoice()
                }
                else if (event.key === Qt.Key_F2) picker.startRename()
                else if (event.key === Qt.Key_F5) fileBrowser.refresh()
                else if (control && shift && event.key === Qt.Key_N) { if (fileBrowser.writable) picker.creatingFolder = true }
                else if (control && event.key === Qt.Key_L) body.editPath(true)
                else if (control && event.key === Qt.Key_F) search.forceActiveFocus()
                else if (control && event.key === Qt.Key_H) fileBrowser.setShowHidden(!fileBrowser.showHidden)
                else if (control && event.key === Qt.Key_A && picker.multiple) picker.selection = picker.entries.filter(function(row) { return !row.isDir }).map(function(row) { return row.path })
                else if (control && event.key === Qt.Key_C) picker.copyPaths()
                else if (!control && !alt && event.text.length === 1 && event.text >= " ") {
                    // Type the first letters of a name to jump to it.
                    picker.typeAhead += event.text.toLowerCase()
                    typeAheadReset.restart()
                    for (let i = 0; i < count; ++i) {
                        if (picker.entries[i].name.toLowerCase().indexOf(picker.typeAhead) === 0) { picker.selectOnly(i); body.reveal(i); break }
                    }
                } else return
                event.accepted = true
            }
        }
    }

    component SortHeader: AbstractButton {
        id: sortHeader
        property string key: ""
        property bool alignRight: false
        implicitHeight: 28; hoverEnabled: true
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        onClicked: fileBrowser.setSort(key)
        Accessible.name: qsTr("Ordina per %1").arg(text)
        contentItem: Row {
            layoutDirection: sortHeader.alignRight ? Qt.RightToLeft : Qt.LeftToRight
            spacing: 4
            Text { text: sortHeader.text; color: fileBrowser.sortKey === sortHeader.key ? appearance.colors.text : appearance.colors.muted; font.family: appearance.uiFont.family; font.pixelSize: Math.max(9, appearance.uiFont.pixelSize - 1); font.weight: Font.DemiBold; textFormat: Text.PlainText; anchors.verticalCenter: parent.verticalCenter }
            KIcon { visible: fileBrowser.sortKey === sortHeader.key; name: "chevron-down"; rotation: fileBrowser.sortDescending ? 0 : 180; width: 12; height: 12; ink: appearance.colors.text; anchors.verticalCenter: parent.verticalCenter }
        }
        background: Item {}
    }
    component DetailKey: Text { color: appearance.colors.muted; font.family: appearance.uiFont.family; font.pixelSize: appearance.uiFont.pixelSize; textFormat: Text.PlainText; Layout.alignment: Qt.AlignTop }
    component DetailValue: Text { color: appearance.colors.text; font.family: appearance.uiFont.family; font.pixelSize: appearance.uiFont.pixelSize; textFormat: Text.PlainText; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true }

    Menu {
        id: entryMenu
        property var row: null
        readonly property bool onFolder: row !== null && row.isDir
        font: appearance.uiFont
        MenuItem { text: entryMenu.onFolder ? qsTr("Apri") : picker.mode === "open" ? qsTr("Scegli") : picker.mode === "save" ? qsTr("Usa questo nome") : qsTr("Apri con l'app predefinita"); visible: entryMenu.row !== null; height: visible ? implicitHeight : 0; onTriggered: { if (entryMenu.row.isDir) picker.go(entryMenu.row.path); else if (picker.mode === "folder") fileBrowser.openExternally(entryMenu.row.path); else picker.activate(picker.indexOfPath(entryMenu.row.path)) } }
        MenuItem { text: qsTr("Usa questa cartella"); visible: picker.mode === "folder" && entryMenu.onFolder; height: visible ? implicitHeight : 0; onTriggered: picker.acceptChoice() }
        MenuItem { text: qsTr("Rinomina"); visible: entryMenu.row !== null; height: visible ? implicitHeight : 0; onTriggered: picker.startRename() }
        MenuItem { text: entryMenu.row !== null ? qsTr("Copia percorso") : qsTr("Copia il percorso della cartella"); onTriggered: entryMenu.row !== null ? fileBrowser.copyText(entryMenu.row.path) : fileBrowser.copyText(fileBrowser.path) }
        MenuItem { text: entryMenu.row !== null && !entryMenu.onFolder ? qsTr("Apri con l'app predefinita") : qsTr("Apri nel gestore file"); onTriggered: fileBrowser.openExternally(entryMenu.row !== null ? entryMenu.row.path : fileBrowser.path) }
        MenuSeparator {}
        MenuItem { text: qsTr("Nuova cartella"); enabled: fileBrowser.writable; onTriggered: picker.creatingFolder = true }
        MenuItem { text: qsTr("Aggiorna"); onTriggered: fileBrowser.refresh() }
    }

    Menu {
        id: viewMenu
        font: appearance.uiFont
        MenuItem { text: qsTr("Ordina per nome"); checkable: true; checked: fileBrowser.sortKey === "name"; onTriggered: fileBrowser.setSort("name") }
        MenuItem { text: qsTr("Ordina per data di modifica"); checkable: true; checked: fileBrowser.sortKey === "modified"; onTriggered: fileBrowser.setSort("modified") }
        MenuItem { text: qsTr("Ordina per dimensione"); checkable: true; checked: fileBrowser.sortKey === "size"; onTriggered: fileBrowser.setSort("size") }
        MenuSeparator {}
        MenuItem { objectName: "fileDialogHiddenToggle"; text: qsTr("Mostra file nascosti · Ctrl+H"); checkable: true; checked: fileBrowser.showHidden; onTriggered: fileBrowser.setShowHidden(!fileBrowser.showHidden) }
        MenuSeparator {}
        MenuItem { text: qsTr("Nuova cartella"); enabled: fileBrowser.writable; onTriggered: picker.creatingFolder = true }
        MenuItem { text: qsTr("Copia il percorso della cartella"); onTriggered: fileBrowser.copyText(fileBrowser.path) }
        MenuItem { text: qsTr("Apri nel gestore file"); onTriggered: fileBrowser.openExternally(fileBrowser.path) }
    }
}
