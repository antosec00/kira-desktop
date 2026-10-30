import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "webDialog"
    readonly property var web: workspace.web
    title: qsTr("Web e browser della chat")
    modal: true; anchors.centerIn: parent
    width: Math.min(800, parent.width - 40)
    height: Math.min(780, parent.height - 60)
    font: appearance.uiFont
    onOpened: web.open()
    onClosed: web.hide()
    Connections { target: dialog.web; function onDismissed() { dialog.close() } }
    background: Rectangle { color: appearance.colors.surface; radius: 14; border.color: appearance.colors.border }
    header: Label {
        text: dialog.title; font.family: appearance.uiFont.family; font.pixelSize: appearance.uiFont.pixelSize; font.bold: true
        color: appearance.colors.text; padding: 18; textFormat: Text.PlainText
    }
    footer: RowLayout {
        spacing: 8
        Item { Layout.fillWidth: true }
        KButton { objectName: "webSave"; text: qsTr("Salva"); primary: true; enabled: dialog.web.canEdit && dialog.web.dirty; Layout.bottomMargin: 12; onClicked: dialog.web.apply() }
        KButton { objectName: "webClose"; text: qsTr("Chiudi"); Layout.rightMargin: 16; Layout.bottomMargin: 12; onClicked: dialog.close() }
    }
    contentItem: ColumnLayout {
        spacing: 10
        RowLayout {
            Layout.fillWidth: true
            ReadText { objectName: "webStatus"; text: dialog.web.status; Layout.fillWidth: true; color: appearance.colors.accent }
            KButton { objectName: "webRefresh"; text: qsTr("Aggiorna"); enabled: dialog.web.available && !dialog.web.busy; onClicked: dialog.web.refresh() }
        }
        ReadText { objectName: "webError"; text: dialog.web.error; visible: text.length > 0; Layout.fillWidth: true; color: appearance.colors.warning }
        ScrollView {
            id: scroll; objectName: "webScroll"
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; contentWidth: availableWidth; rightPadding: 16
            ScrollBar.vertical: KScrollBar { parent: scroll; x: scroll.width - width; y: 0; height: scroll.height; orientation: Qt.Vertical }
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: scroll.availableWidth; spacing: 12
                ReadText { text: qsTr("Ricerca e lettura web"); font.bold: true; Layout.fillWidth: true; visible: workspace.backend === "openai_chatgpt" }
                KComboBox {
                    objectName: "webSearchMode"; Layout.fillWidth: true
                    visible: workspace.backend === "openai_chatgpt"
                    Accessible.name: qsTr("Modalità della ricerca web")
                    model: [
                        {value: "disabled", label: qsTr("Ricerca disattivata")},
                        {value: "cached", label: qsTr("Contenuti indicizzati")},
                        {value: "live", label: qsTr("Accesso live")}
                    ]
                    textRole: "label"; valueRole: "value"
                    currentIndex: indexOfValue(dialog.web.settings.web_search)
                    enabled: dialog.web.canEdit
                    onActivated: dialog.web.setSearchMode(currentValue)
                }
                ReadText { text: qsTr("La ricerca usa lo strumento OpenAI, se disponibile per il modello e l’account. Accesso live consente di recuperare pagine aggiornate; Contenuti indicizzati limita la ricerca ai contenuti già disponibili al servizio."); Layout.fillWidth: true; color: appearance.colors.muted; visible: workspace.backend === "openai_chatgpt" }
                ReadText { text: qsTr("Con Ollama, il browser usa strumenti separati da file e comandi del progetto. Il primo uso verifica le chiamate agli strumenti del modello; screenshot e controllo visivo richiedono anche input immagini. La ricerca web ospitata da OpenAI non è disponibile."); Layout.fillWidth: true; color: appearance.colors.muted; visible: workspace.backend === "ollama" }
                ReadText { text: qsTr("Con DeepSeek Account, Kira usa i propri strumenti Browser e Computer: le pagine sono aperte nel browser isolato di Kira e le azioni rispettano i permessi del progetto."); Layout.fillWidth: true; color: appearance.colors.muted; visible: workspace.backend === "deepseek_account" }
                ReadText { text: qsTr("Con Google Cloud · Vertex AI, Kira usa i propri strumenti Browser e Computer con input visivo e multimodale: le pagine sono aperte nel browser isolato di Kira e le azioni rispettano i permessi del progetto."); Layout.fillWidth: true; color: appearance.colors.muted; visible: workspace.backend === "google_cloud" }
                ReadText { objectName: "webClaudeCodeNote"; text: qsTr("Con Claude Code, Kira offre i propri strumenti Browser e Computer tramite il server MCP interno: le pagine si aprono nel browser isolato di Kira, le azioni rispettano i permessi del progetto e gli screenshot arrivano a Claude Code come immagini."); Layout.fillWidth: true; color: appearance.colors.muted; visible: workspace.backend === "claude_code" }
                ReadText { text: qsTr("Browser visuale"); font.bold: true; Layout.fillWidth: true }
                CheckBox {
                    objectName: "webBrowserEnabled"; text: qsTr("Consenti il browser per questa chat")
                    Layout.fillWidth: true; font: appearance.uiFont
                    checked: dialog.web.settings.browser_enabled; enabled: dialog.web.canEdit
                    onClicked: dialog.web.setBrowserEnabled(checked)
                    HoverHandler { cursorShape: appearance.pointerCursors && parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
                }
                ReadText { text: qsTr("Kira può aprire pagine pubbliche, leggere il contenuto, vedere la pagina e usare clic, testo e scorrimento. Il browser usa un profilo isolato dai tuoi browser personali."); Layout.fillWidth: true; color: appearance.colors.muted }
                ReadText { objectName: "webComputerTitle"; text: qsTr("Computer di Kira"); font.bold: true; Layout.fillWidth: true }
                ReadText { objectName: "webComputerDescription"; text: qsTr("Il modello può osservare una schermata del browser, usare mouse e tastiera e controllare il risultato con una nuova schermata. Le azioni restano in questa chat; quando prendi il controllo, il modello attende."); Layout.fillWidth: true; color: appearance.colors.muted }
                ReadText { text: qsTr("La lettura è disponibile anche in sola lettura e nelle altre modalità. Clic e inserimento di testo richiedono Esegui, un progetto con scrittura consentita e il consenso per l’azione o per questa chat. Anche il login manuale richiede Esegui e un progetto con scrittura consentita."); Layout.fillWidth: true; color: appearance.colors.muted }
                CheckBox {
                    objectName: "webAlwaysAllowActions"
                    text: qsTr("Consenti sempre le azioni dell’agente in questa chat")
                    Layout.fillWidth: true; font: appearance.uiFont
                    checked: dialog.web.actionApprovalAlways
                    enabled: dialog.web.canChangeActionApproval
                    onClicked: dialog.web.setActionApprovalAlways(checked)
                    HoverHandler { cursorShape: appearance.pointerCursors && parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
                }
                ReadText { text: qsTr("Il consenso si salva subito per questa chat. Puoi revocarlo anche durante il lavoro; login, passkey e comandi mantengono le proprie conferme."); Layout.fillWidth: true; color: appearance.colors.muted }
                CheckBox {
                    objectName: "webPersistentEnabled"; text: qsTr("Conserva il profilo del browser per questa chat")
                    Layout.fillWidth: true; font: appearance.uiFont
                    checked: dialog.web.settings.browser_persistent
                    enabled: dialog.web.canEdit && dialog.web.settings.browser_enabled
                    onClicked: dialog.web.setPersistentEnabled(checked)
                    HoverHandler { cursorShape: appearance.pointerCursors && parent.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }
                }
                ReadText { text: qsTr("Per mantenere un accesso, abilita e salva il profilo prima della richiesta. Chiedi poi a Kira di aprire il sito per il login manuale: inserisci le credenziali nel browser integrato e premi Ho finito quando hai terminato. Cookie e sessioni restano nel profilo separato della chat."); Layout.fillWidth: true; color: appearance.colors.muted }
                ReadText { text: qsTr("Senza conservazione, il profilo temporaneo viene eliminato quando chiudi il browser. Disattivare la conservazione non cancella un profilo già salvato."); Layout.fillWidth: true; color: appearance.colors.muted }
                ReadText { objectName: "webToolsDisabled"; text: qsTr("Abilita Strumenti nei controlli del modello per usare ricerca e browser nelle richieste."); visible: !workspace.toolsEnabled && workspace.backend === "openai_chatgpt"; Layout.fillWidth: true; color: appearance.colors.warning }
                ReadText { objectName: "webOllamaBrowserTools"; text: qsTr("Con Ollama, il browser può funzionare anche con Strumenti disattivati. Questo non abilita strumenti per i file o i comandi del progetto."); visible: !workspace.toolsEnabled && workspace.backend === "ollama"; Layout.fillWidth: true; color: appearance.colors.muted }
                ReadText { text: qsTr("Le altre impostazioni si possono modificare quando il progetto non ha attività in corso."); visible: dialog.web.loaded && !dialog.web.canEdit && !dialog.web.busy; Layout.fillWidth: true; color: appearance.colors.muted }
                ColumnLayout {
                    visible: dialog.web.loaded && !dialog.web.browser.available
                    Layout.fillWidth: true; spacing: 6
                    ReadText { text: qsTr("Il browser richiede un’installazione iniziale esplicita. Esegui questo comando, poi premi Aggiorna:"); Layout.fillWidth: true; color: appearance.colors.warning }
                    ReadText { objectName: "webInstallCommand"; text: "kira-desktop browser-install"; Layout.fillWidth: true }
                }
                ReadText { text: dialog.web.browser.error; visible: text.length > 0; Layout.fillWidth: true; color: appearance.colors.warning }
                ReadText { text: qsTr("Ultima pagina osservata"); font.bold: true; Layout.fillWidth: true }
                ReadText { objectName: "webPageTitle"; text: dialog.web.browser.title; visible: text.length > 0; Layout.fillWidth: true }
                ReadText { objectName: "webPageUrl"; text: dialog.web.browser.url; visible: text.length > 0; Layout.fillWidth: true; color: appearance.colors.muted }
                Image {
                    objectName: "webPreview"
                    Layout.fillWidth: true; Layout.preferredHeight: Math.max(160, Math.min(360, width * 0.625))
                    source: dialog.web.browser.preview_url
                    sourceSize.width: 1100
                    fillMode: Image.PreserveAspectFit; asynchronous: true; cache: false
                    visible: source.toString().length > 0
                    Accessible.name: qsTr("Anteprima della pagina osservata dal browser")
                    Accessible.role: Accessible.Graphic
                }
                ReadText { text: qsTr("Nessuna anteprima disponibile. Verrà mostrata quando Kira userà il browser."); visible: dialog.web.browser.preview_url.length === 0; Layout.fillWidth: true; color: appearance.colors.muted }
                ReadText { text: qsTr("L’anteprima si aggiorna dopo le azioni del browser. Aggiorna rilegge soltanto lo stato disponibile, senza navigare."); Layout.fillWidth: true; color: appearance.colors.muted }
            }
        }
    }
}
