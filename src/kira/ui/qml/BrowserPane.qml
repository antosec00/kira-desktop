import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: pane
    objectName: "browserPane"
    readonly property var web: workspace.web
    property bool compact: width < 600
    readonly property string authenticationNotice: {
        var browser = web.browser
        if (browser.auth_pending || !browser.auth_origin
                || !(browser.url === browser.auth_origin || browser.url.indexOf(browser.auth_origin + "/") === 0))
            return ""
        if (browser.auth_result === "unsupported")
            return qsTr("Questa richiesta passkey usa opzioni non ancora supportate dal collegamento a Windows.")
        if (browser.auth_result === "cancelled")
            return qsTr("Autenticazione annullata. Puoi riprovare dal sito.")
        if (browser.auth_result === "failed" || browser.auth_result === "unconfirmed")
            return qsTr("Autenticazione non confermata. Controlla il sito prima di riprovare.")
        return ""
    }
    // The browser worker names known view failures with a code: those are shown in
    // the interface language, and only an unknown failure keeps the worker's text.
    readonly property string surfaceError: web.surfaceErrorCode === "view_unavailable"
        ? qsTr("La vista completa del browser non è disponibile. Chiudi e riapri il browser dopo aver salvato il tuo lavoro.")
        : web.browser.surface_error || ""
    signal settingsRequested()
    color: appearance.colors.surface
    border.color: "transparent"
    clip: true

    function modifiers(value) {
        return ((value & Qt.AltModifier) ? 1 : 0) | ((value & Qt.ControlModifier) ? 2 : 0)
                | ((value & Qt.MetaModifier) ? 4 : 0) | ((value & Qt.ShiftModifier) ? 8 : 0)
    }
    function mouseButton(value) {
        return value === Qt.LeftButton ? "left" : value === Qt.RightButton ? "right" : value === Qt.MiddleButton ? "middle" : "none"
    }
    function mouseButtons(value) {
        return ((value & Qt.LeftButton) ? 1 : 0) | ((value & Qt.RightButton) ? 2 : 0) | ((value & Qt.MiddleButton) ? 4 : 0)
    }
    function pointer(kind, event, dx, dy) {
        web.sendMouse(kind, event.x, event.y, viewport.width, viewport.height,
                      mouseButton(event.button || 0), mouseButtons(event.buttons || 0), dx || 0, dy || 0, modifiers(event.modifiers || 0))
    }
    function keyName(event) {
        var names = {}
        names[Qt.Key_Return] = "Enter"; names[Qt.Key_Enter] = "Enter"
        names[Qt.Key_Backspace] = "Backspace"; names[Qt.Key_Delete] = "Delete"
        names[Qt.Key_Tab] = "Tab"; names[Qt.Key_Backtab] = "Tab"
        names[Qt.Key_Escape] = "Escape"; names[Qt.Key_Left] = "ArrowLeft"
        names[Qt.Key_Right] = "ArrowRight"; names[Qt.Key_Up] = "ArrowUp"
        names[Qt.Key_Down] = "ArrowDown"; names[Qt.Key_Home] = "Home"
        names[Qt.Key_End] = "End"; names[Qt.Key_PageUp] = "PageUp"; names[Qt.Key_PageDown] = "PageDown"
        names[Qt.Key_Shift] = "Shift"; names[Qt.Key_Control] = "Control"
        names[Qt.Key_Alt] = "Alt"; names[Qt.Key_Meta] = "Meta"
        return names[event.key] || ((event.key >= Qt.Key_A && event.key <= Qt.Key_Z) ? String.fromCharCode(event.key).toLowerCase() : "")
    }
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 6
        RowLayout {
            Layout.fillWidth: true
            KIcon { name: "browser"; ink: appearance.colors.accent; Layout.preferredWidth: 18; Layout.preferredHeight: 18 }
            Label { text: qsTr("Browser"); font: appearance.uiFont; color: appearance.colors.text }
            Label {
                objectName: "browserNativeBadge"
                Layout.fillWidth: true
                visible: pane.web.nativeSurface && pane.web.controlling
                text: qsTr("Browser completo")
                font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2)
                color: appearance.colors.muted
            }
            Item { visible: !pane.web.nativeSurface || !pane.web.controlling; Layout.fillWidth: true }
            KButton { objectName: "browserSettings"; subtle: true; text: "⚙"; toolTipText: qsTr("Impostazioni web"); Accessible.name: toolTipText; implicitWidth: 36; onClicked: pane.settingsRequested() }
            KButton { objectName: "browserExpand"; subtle: true; text: pane.web.expanded ? "↙" : "↗"; toolTipText: pane.web.expanded ? qsTr("Affianca alla chat") : qsTr("Espandi browser"); Accessible.name: toolTipText; implicitWidth: 36; onClicked: pane.web.toggleExpanded() }
            KButton { objectName: "browserHide"; subtle: true; text: "×"; toolTipText: qsTr("Chiudi browser"); Accessible.name: toolTipText; implicitWidth: 36; enabled: !pane.web.lifecycleBusy && !pane.web.commandBusy; onClicked: pane.web.closeBrowser() }
        }
        RowLayout {
            Layout.fillWidth: true
            Flickable {
                Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredHeight: tabsRow.implicitHeight
                clip: true; contentWidth: tabsRow.implicitWidth; contentHeight: height
                Row {
                    id: tabsRow; spacing: 4
                    Repeater {
                        model: pane.web.browser.tabs
                        Row {
                            required property var modelData
                            KButton {
                                text: modelData.title || modelData.url || qsTr("Nuova scheda")
                                width: Math.min(210, Math.max(80, implicitWidth)); subtle: true; checked: modelData.tab_id === pane.web.browser.tab_id
                                enabled: pane.web.controlling; toolTipText: modelData.url
                                onClicked: pane.web.command("ui_select_tab", {tab_id: modelData.tab_id})
                            }
                            KButton { text: "×"; implicitWidth: 28; subtle: true; enabled: pane.web.controlling; toolTipText: qsTr("Chiudi scheda"); Accessible.name: toolTipText; onClicked: pane.web.command("ui_close_tab", {tab_id: modelData.tab_id}) }
                        }
                    }
                }
            }
            KButton { objectName: "browserNewTab"; subtle: true; text: "+"; implicitWidth: 32; enabled: pane.web.controlling; toolTipText: qsTr("Nuova scheda"); Accessible.name: toolTipText; onClicked: pane.web.command("ui_new_tab", {}) }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 4
            KButton { objectName: "browserBack"; subtle: true; text: "←"; implicitWidth: 32; enabled: pane.web.controlling && pane.web.browser.can_back; toolTipText: qsTr("Indietro"); Accessible.name: toolTipText; onClicked: pane.web.command("ui_back", {}) }
            KButton { objectName: "browserForward"; subtle: true; text: "→"; implicitWidth: 32; enabled: pane.web.controlling && pane.web.browser.can_forward; toolTipText: qsTr("Avanti"); Accessible.name: toolTipText; onClicked: pane.web.command("ui_forward", {}) }
            KButton { objectName: "browserReload"; subtle: true; text: "↻"; implicitWidth: 32; enabled: pane.web.controlling; toolTipText: qsTr("Ricarica pagina"); Accessible.name: toolTipText; onClicked: pane.web.command("ui_reload", {}) }
            TextField {
                id: address; objectName: "browserAddress"
                Layout.fillWidth: true; Layout.minimumWidth: 40
                font: appearance.uiFont; selectByMouse: true
                readOnly: !pane.web.controlling || pane.web.lifecycleBusy
                placeholderText: qsTr("Indirizzo della pagina")
                Accessible.name: qsTr("Indirizzo della pagina")
                property bool editingAddress: false
                property string observedScope: ""
                property string observedUrl: ""
                function syncAddress() {
                    var browser = pane.web.browser
                    var scope = JSON.stringify([workspace.project.id || "", workspace.sessionId,
                                                browser.browser_id, browser.tab_id])
                    var url = browser.url || ""
                    var scopeChanged = scope !== observedScope
                    var controlReturned = editingAddress && !pane.web.controlling
                    if (scopeChanged || controlReturned || (!editingAddress && url !== observedUrl)) {
                        editingAddress = false
                        text = url
                    }
                    observedScope = scope
                    observedUrl = url
                }
                Component.onCompleted: syncAddress()
                Connections { target: pane.web; function onChanged() { address.syncAddress() } }
                onTextEdited: editingAddress = true
                onAccepted: {
                    if (pane.web.navigateAddress(text)) {
                        editingAddress = false
                        keyboard.forceActiveFocus()
                    }
                }
                Keys.onEscapePressed: {
                    editingAddress = false
                    text = pane.web.browser.url
                    keyboard.forceActiveFocus()
                }
            }
        }
        ProgressBar { Layout.fillWidth: true; Layout.preferredHeight: 3; visible: pane.web.browser.loading; indeterminate: true }
        Rectangle {
            objectName: "browserAuthenticationBanner"
            visible: pane.web.browser.auth_pending === true
            Layout.fillWidth: true; implicitHeight: authenticationColumn.implicitHeight + 16
            color: appearance.colors.surfaceAlt; border.color: appearance.colors.warning; radius: 8
            ColumnLayout {
                id: authenticationColumn; anchors.fill: parent; anchors.margins: 8
                ReadText { Layout.fillWidth: true; text: qsTr("Autenticazione per %1").arg(pane.web.browser.auth_origin || ""); color: appearance.colors.accent }
                ReadText {
                    Layout.fillWidth: true
                    text: pane.web.browser.auth_needs_manual ? qsTr("La pagina richiede una tua conferma. Continua quando sei pronto: Kira aspetta e non inserisce PIN o credenziali.")
                        : pane.web.browser.auth_provider === "windows" ? qsTr("Completa la richiesta nella finestra sicura di Windows. Il PIN e la biometria restano gestiti da Windows.")
                        : qsTr("Completa o annulla la richiesta nella finestra del browser.")
                }
                Flow {
                    Layout.fillWidth: true; spacing: 6
                    KButton {
                        objectName: "browserAuthenticationContinue"
                        visible: pane.web.browser.auth_needs_manual === true
                        text: qsTr("Continua autenticazione"); primary: true
                        enabled: !pane.web.lifecycleBusy
                        onClicked: pane.web.command("ui_takeover", {})
                    }
                    KButton {
                        objectName: "browserAuthenticationKeepControl"
                        visible: pane.web.manualApproval
                        text: qsTr("Mantieni il controllo")
                        enabled: !workspace.approvalBusy
                        toolTipText: qsTr("Il browser rimane aperto; l’agente non riprende automaticamente.")
                        onClicked: workspace.approve("decline")
                    }
                }
            }
        }
        Rectangle {
            objectName: "browserLoginBanner"
            visible: pane.web.manualApproval && pane.web.browser.auth_pending !== true
            Layout.fillWidth: true; implicitHeight: loginColumn.implicitHeight + 16
            color: appearance.colors.surfaceAlt; border.color: appearance.colors.warning; radius: 8
            ColumnLayout {
                id: loginColumn; anchors.fill: parent; anchors.margins: 8
                ReadText { text: qsTr("Completa l’accesso qui nel browser. Le credenziali vanno inserite solo sul sito. Quando hai finito restituisci il controllo a Kira."); Layout.fillWidth: true }
                ReadText { text: qsTr("Passkey e PIN richiedono la tua conferma manuale nella finestra del browser."); Layout.fillWidth: true; visible: pane.web.nativeSurface }
                RowLayout {
                    Item { Layout.fillWidth: true }
                    KButton { objectName: "browserLoginCancel"; text: qsTr("Mantieni il controllo"); enabled: !workspace.approvalBusy; toolTipText: qsTr("Il browser rimane aperto; l’agente non riprende automaticamente."); onClicked: workspace.approve("decline") }
                    KButton { objectName: "browserLoginDone"; text: qsTr("Ho finito"); primary: true; enabled: pane.web.browser.auth_pending !== true && !workspace.approvalBusy; onClicked: workspace.approve("accept") }
                }
            }
        }
        ReadText {
            objectName: "browserControlWait"
            visible: workspace.browserControlWaiting
            text: qsTr("Kira attende che tu restituisca il controllo del browser.")
            color: appearance.colors.accent
            Layout.fillWidth: true
        }
        Rectangle {
            objectName: "browserOwnershipBand"
            Layout.fillWidth: true
            implicitHeight: ownershipRow.implicitHeight + 12
            radius: 10; color: appearance.colors.surfaceAlt
            RowLayout {
                id: ownershipRow
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 6; spacing: 6
                Rectangle { Layout.preferredWidth: 6; Layout.preferredHeight: 6; radius: 3; color: appearance.colors.accent }
                Label {
                    objectName: "browserActionApprovalStatus"
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    readonly property string controlLabel: pane.web.controlling ? qsTr("Controllo tuo") : qsTr("Controllo di Kira")
                    text: pane.web.actionApprovalAlways ? qsTr("%1 · azioni consentite").arg(controlLabel) : controlLabel
                    font: appearance.uiFont; color: appearance.colors.accent; wrapMode: Text.Wrap
                }
                KButton {
                    objectName: "browserTakeControl"
                    text: pane.web.controlling ? qsTr("Restituisci a Kira") : qsTr("Prendi il controllo")
                    enabled: pane.web.browser.active && !pane.web.lifecycleBusy && !pane.web.manualApproval && pane.web.browser.auth_pending !== true
                    primary: !pane.web.controlling || workspace.browserControlWaiting
                    onClicked: pane.web.command(pane.web.controlling ? "ui_resume" : "ui_takeover", {})
                }
                KButton { objectName: "browserStop"; text: qsTr("Chiudi browser"); subtle: true; enabled: pane.web.browser.active && !pane.web.lifecycleBusy; onClicked: pane.web.closeBrowser() }
            }
        }
        ReadText { objectName: "browserError"; Layout.fillWidth: true; visible: text.length > 0; text: pane.web.error || pane.surfaceError || pane.authenticationNotice || pane.web.browser.error; color: appearance.colors.warning }
        Rectangle {
            id: viewport; objectName: "browserViewport"
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 80
            color: "#ffffff"; clip: true
            onWidthChanged: pane.web.setViewport(Math.floor(width), Math.floor(height))
            onHeightChanged: pane.web.setViewport(Math.floor(width), Math.floor(height))
            Image {
                objectName: "browserLiveFrame"
                anchors.fill: parent; source: pane.web.frameUrl
                fillMode: Image.PreserveAspectFit; cache: false; asynchronous: false
                Accessible.name: qsTr("Pagina del browser integrato")
                Accessible.role: Accessible.Graphic
            }
            ReadText {
                anchors.centerIn: parent; width: Math.max(100, parent.width - 40)
                visible: !pane.web.frameUrl.length
                color: "#333333"
                text: pane.web.browser.active ? qsTr("Caricamento della pagina…") : qsTr("Il browser è chiuso. Aprilo per navigare in questa chat.")
            }
            KButton { anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: parent.bottom; anchors.bottomMargin: 20; text: qsTr("Apri browser"); visible: !pane.web.browser.active; enabled: pane.web.available && !pane.web.lifecycleBusy; onClicked: pane.web.showBrowser() }
            MouseArea {
                id: pointer; objectName: "browserPointer"
                anchors.fill: parent; enabled: pane.web.controlling && !pane.web.lifecycleBusy && pane.web.frameUrl.length > 0
                hoverEnabled: true; acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
                preventStealing: true
                onPressed: function(mouse) { keyboard.forceActiveFocus(); pane.pointer("press", mouse, 0, 0) }
                onReleased: function(mouse) { pane.pointer("release", mouse, 0, 0) }
                onPositionChanged: function(mouse) { pane.pointer("move", mouse, 0, 0) }
                onWheel: function(wheel) { pane.pointer("wheel", wheel, -wheel.angleDelta.x, -wheel.angleDelta.y); wheel.accepted = true }
            }
            TextInput {
                id: keyboard; objectName: "browserKeyboard"
                property var forwardedKeys: ({})
                property string inputScope: ""
                function syncSurface() {
                    var scope = String(pane.web.surfaceRevision) + ":" + String(pane.web.controlling)
                    if (scope !== inputScope) {
                        inputScope = scope
                        forwardedKeys = ({})
                        if (activeFocus) Qt.inputMethod.reset()
                        text = ""
                    }
                }
                Component.onCompleted: syncSurface()
                Connections { target: pane.web; function onChanged() { keyboard.syncSurface() } }
                width: 1; height: 1; opacity: 0.01
                enabled: pane.web.controlling && !pane.web.lifecycleBusy && pane.web.frameUrl.length > 0
                inputMethodHints: Qt.ImhNoPredictiveText | Qt.ImhSensitiveData | Qt.ImhNoAutoUppercase
                onTextEdited: { if (text.length) { pane.web.sendText(text); text = "" } }
                Keys.onPressed: function(event) {
                    if (event.matches(StandardKey.Paste)) { paste(); event.accepted = true; return }
                    if (event.matches(StandardKey.Copy)) { pane.web.command("ui_copy", {}); event.accepted = true; return }
                    var key = pane.keyName(event)
                    var special = event.key >= Qt.Key_Escape || (event.modifiers & (Qt.ControlModifier | Qt.MetaModifier | Qt.AltModifier))
                    if (key && special) {
                        if (event.isAutoRepeat && !forwardedKeys[key]) { event.accepted = true; return }
                        forwardedKeys[key] = true
                        pane.web.sendKey(key, "", pane.modifiers(event.modifiers), true)
                        event.accepted = true
                    }
                }
                Keys.onReleased: function(event) {
                    if (event.matches(StandardKey.Paste) || event.matches(StandardKey.Copy)) { event.accepted = true; return }
                    var key = pane.keyName(event)
                    var special = event.key >= Qt.Key_Escape || (event.modifiers & (Qt.ControlModifier | Qt.MetaModifier | Qt.AltModifier))
                    if (key && special && forwardedKeys[key]) {
                        delete forwardedKeys[key]
                        pane.web.sendKey(key, "", pane.modifiers(event.modifiers), false)
                        event.accepted = true
                    }
                }
            }
        }
        Label { Layout.fillWidth: true; elide: Text.ElideRight; text: pane.web.browser.title; font: appearance.uiFont; color: appearance.colors.muted; textFormat: Text.PlainText }
    }
}
