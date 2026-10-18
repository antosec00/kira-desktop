import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    objectName: "appearancePane"
    spacing: 14
    readonly property bool narrow: width < 530
    property string transferState: ""
    readonly property string transferMessage: transferState === "copied" ? qsTr("Tema copiato negli appunti.") : transferState === "reset" ? qsTr("Preferenze di aspetto ripristinate.") : transferState === "imported" ? qsTr("Tema importato.") : ""
    readonly property var styleKeys: ["normal", "medium", "semibold", "bold", "italic"]
    onTransferMessageChanged: if (transferMessage) transferTimer.restart()

    Timer { id: transferTimer; interval: 4500; onTriggered: pane.transferState = "" }

    function adjustedFont(base, size, weight) {
        return Qt.font({family: base.family, pixelSize: size, weight: weight, italic: base.italic})
    }

    component Caption: Text {
        Layout.fillWidth: true
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: appearance.colors.text
        font: appearance.uiFont
    }
    component Description: Text {
        Layout.fillWidth: true
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: appearance.colors.muted
        font: pane.adjustedFont(appearance.uiFont, Math.max(11, appearance.uiFont.pixelSize - 1), appearance.uiFont.weight)
        lineHeight: 1.2
    }
    component SectionHeading: Caption {
        Layout.topMargin: 8
        font: pane.adjustedFont(appearance.uiFont, appearance.uiFont.pixelSize + 2, Font.DemiBold)
    }
    component Toggle: Switch {
        id: toggle
        implicitWidth: 60
        implicitHeight: 40
        hoverEnabled: true
        indicator: Rectangle {
            x: toggle.leftPadding
            y: (toggle.height - height) / 2
            implicitWidth: 42
            implicitHeight: 24
            radius: 12
            color: toggle.checked ? appearance.colors.accent : appearance.colors.surfaceAlt
            border.width: toggle.visualFocus ? 2 : 1
            border.color: toggle.visualFocus || toggle.checked ? appearance.colors.accent : appearance.colors.border
            Rectangle {
                x: toggle.checked ? parent.width - width - 4 : 4
                anchors.verticalCenter: parent.verticalCenter
                width: 16
                height: 16
                radius: 8
                color: toggle.checked ? appearance.colors.accentText : appearance.colors.muted
            }
        }
        HoverHandler { cursorShape: appearance.pointerCursors ? Qt.PointingHandCursor : Qt.ArrowCursor }
    }
    component CheckRow: RowLayout {
        id: row
        property string title
        property string note: ""
        property string option
        property string controlName
        Layout.fillWidth: true
        spacing: 16
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4
            Caption { text: row.title }
            Description { text: row.note; visible: text.length > 0 }
        }
        Toggle {
            objectName: row.controlName
            checked: !!appearance.values[row.option]
            onToggled: appearance.setOption(row.option, checked)
            Accessible.name: row.title
        }
    }
    component ColorRow: ColumnLayout {
        id: colorRow
        property string title
        property string styleKey
        property string colorRole
        readonly property string selectedColor: appearance.style[styleKey] || appearance.colors[colorRole]
        Layout.fillWidth: true
        spacing: 4
        GridLayout {
            Layout.fillWidth: true
            columns: pane.narrow ? 1 : 2
            columnSpacing: 18
            rowSpacing: 6
            Caption { text: colorRow.title }
            RowLayout {
                Layout.fillWidth: pane.narrow
                Layout.preferredWidth: 264
                spacing: 8
                Rectangle {
                    Layout.preferredWidth: 32
                    Layout.preferredHeight: 32
                    radius: 8
                    color: colorRow.selectedColor
                    border.color: appearance.colors.border
                    border.width: 1
                    Accessible.role: Accessible.StaticText
                    Accessible.name: colorRow.title + " " + colorRow.selectedColor
                }
                TextField {
                    id: hexInput
                    objectName: "appearanceColor_" + colorRow.styleKey
                    Layout.fillWidth: true
                    Layout.minimumWidth: 108
                    font: appearance.codeFont
                    selectByMouse: true
                    maximumLength: 7
                    placeholderText: "#RRGGBB"
                    validator: RegularExpressionValidator { regularExpression: /^#[0-9a-fA-F]{6}$/ }
                    onEditingFinished: if (acceptableInput) appearance.setStyle(colorRow.styleKey, text)
                    Accessible.name: qsTr("%1, colore esadecimale").arg(colorRow.title)
                    Binding {
                        target: hexInput
                        property: "text"
                        value: colorRow.selectedColor
                        when: !hexInput.activeFocus
                        restoreMode: Binding.RestoreNone
                    }
                }
                KButton {
                    text: qsTr("Auto")
                    enabled: !!appearance.style[colorRow.styleKey]
                    onClicked: appearance.setStyle(colorRow.styleKey, "")
                    Accessible.name: qsTr("Ripristina %1 del tema").arg(colorRow.title.toLowerCase())
                    toolTipText: qsTr("Usa il colore del tema selezionato")
                }
            }
        }
        Description {
            visible: hexInput.activeFocus && !hexInput.acceptableInput
            text: qsTr("Inserisci # seguito da sei cifre esadecimali, per esempio #527ACC.")
            color: appearance.colors.warning
        }
    }
    component FontCard: Frame {
        id: card
        property string title
        property string fontScope
        property string defaultLabel
        property string defaultFamily
        property int minimumSize: 12
        property int maximumSize: 20
        readonly property string savedFamily: appearance.style[fontScope + "_family"]
        readonly property bool missingFamily: ["system", "interface", "monospace"].indexOf(savedFamily) < 0
            && appearance.fontFamilies.indexOf(savedFamily) < 0
        readonly property var families: {
            let options = [{label: defaultLabel, value: defaultFamily}].concat(
                appearance.fontFamilies.map(function(family) { return {label: family, value: family}; }))
            if (options.findIndex(function(family) { return family.value === savedFamily; }) < 0) {
                let names = {system: qsTr("Carattere di sistema"), interface: qsTr("Come l’interfaccia"), monospace: qsTr("Monospazio di sistema")}
                options.splice(1, 0, {label: names[savedFamily] || qsTr("%1 · non installato").arg(savedFamily), value: savedFamily})
            }
            return options
        }
        Layout.fillWidth: true
        padding: 14
        background: Rectangle {
            color: appearance.colors.surfaceAlt
            radius: 12
            border.color: "transparent"
        }
        ColumnLayout {
            anchors.fill: parent
            spacing: 10
            Caption { text: card.title; font: pane.adjustedFont(appearance.uiFont, appearance.uiFont.pixelSize, Font.DemiBold) }
            KComboBox {
                objectName: "appearanceFont_" + card.fontScope
                Layout.fillWidth: true
                model: card.families
                textRole: "label"
                valueRole: "value"
                currentIndex: card.families.findIndex(function(family) {
                    return family.value === appearance.style[card.fontScope + "_family"];
                })
                onActivated: appearance.setStyle(card.fontScope + "_family", currentValue)
                Accessible.name: qsTr("Carattere %1").arg(card.title.toLowerCase())
            }
            Description {
                visible: card.missingFamily
                text: qsTr("Il carattere salvato non è installato: viene usato un sostituto disponibile sul sistema.")
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                KComboBox {
                    objectName: "appearanceFontStyle_" + card.fontScope
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    model: [qsTr("Normale"), qsTr("Medio"), qsTr("Semigrassetto"), qsTr("Grassetto"), qsTr("Corsivo")]
                    currentIndex: pane.styleKeys.indexOf(appearance.style[card.fontScope + "_style"])
                    onActivated: appearance.setStyle(card.fontScope + "_style", pane.styleKeys[currentIndex])
                    Accessible.name: qsTr("Stile %1").arg(card.title.toLowerCase())
                }
                SpinBox {
                    objectName: card.fontScope === "content" ? "chatFontChoice" : "appearanceFontSize_" + card.fontScope
                    Layout.preferredWidth: 132
                    from: card.minimumSize
                    to: card.maximumSize
                    value: appearance.values[card.fontScope + "_size"]
                    onValueModified: appearance.setOption(card.fontScope + "_size", value)
                    Accessible.name: qsTr("Dimensione %1 in pixel").arg(card.title.toLowerCase())
                }
                Caption { text: "px"; Layout.fillWidth: false }
            }
        }
    }

    SectionHeading { text: qsTr("Aspetto"); Layout.topMargin: 0 }
    Description { text: qsTr("Colori, caratteri e dettagli si applicano subito, anche durante il lavoro.") }
    GridLayout {
        Layout.fillWidth: true
        columns: pane.narrow ? 1 : 2
        columnSpacing: 18
        rowSpacing: 8
        Caption { text: qsTr("Modalità") }
        KComboBox {
            objectName: "appearanceModeChoice"
            Layout.fillWidth: pane.narrow
            Layout.preferredWidth: 264
            model: [qsTr("Segui il sistema"), qsTr("Chiara"), qsTr("Scura")]
            currentIndex: ["system", "light", "dark"].indexOf(appearance.values.mode)
            onActivated: appearance.setOption("mode", ["system", "light", "dark"][currentIndex])
            Accessible.name: qsTr("Modalità chiara o scura")
        }
    }
    CheckRow {
        title: qsTr("Personalizza chiaro e scuro separatamente")
        note: qsTr("Conserva colori e caratteri diversi per le due modalità.")
        option: "separate"
        controlName: "separateThemesChoice"
    }
    RowLayout {
        Layout.fillWidth: true
        visible: appearance.values.separate
        Description { text: appearance.dark ? qsTr("Stai modificando il tema scuro.") : qsTr("Stai modificando il tema chiaro.") }
        KButton {
            objectName: "editLightThemeButton"
            text: qsTr("Chiaro")
            checked: !appearance.dark
            onClicked: appearance.setOption("mode", "light")
        }
        KButton {
            objectName: "editDarkThemeButton"
            text: qsTr("Scuro")
            checked: appearance.dark
            onClicked: appearance.setOption("mode", "dark")
        }
    }
    GridLayout {
        Layout.fillWidth: true
        columns: pane.narrow ? 1 : 2
        columnSpacing: 18
        rowSpacing: 8
        Caption { text: qsTr("Tema") }
        KComboBox {
            objectName: "appearanceThemeChoice"
            Layout.fillWidth: pane.narrow
            Layout.preferredWidth: 264
            model: ["Kira", qsTr("Neutro"), qsTr("Caldo")]
            currentIndex: ["kira", "neutral", "warm"].indexOf(appearance.style.theme)
            onActivated: appearance.setStyle("theme", ["kira", "neutral", "warm"][currentIndex])
            Accessible.name: qsTr("Tema dei colori")
        }
    }
    ColorRow { title: qsTr("Colore principale"); styleKey: "accent"; colorRole: "accent" }
    ColorRow { title: qsTr("Sfondo"); styleKey: "background"; colorRole: "background" }
    ColorRow { title: qsTr("Testo"); styleKey: "foreground"; colorRole: "text" }
    Description { text: qsTr("Conferma un colore con Invio. Auto ripristina il colore del tema.") }

    SectionHeading { text: qsTr("Anteprima") }
    Rectangle {
        objectName: "appearancePreview"
        Layout.fillWidth: true
        implicitHeight: previewColumn.implicitHeight + 32
        color: appearance.colors.background
        border.color: appearance.colors.border
        radius: 12
        ColumnLayout {
            id: previewColumn
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.margins: 16
            spacing: 12
            RowLayout {
                Layout.fillWidth: true
                Rectangle { width: 10; height: 10; radius: 5; color: appearance.colors.accent }
                Caption { text: qsTr("Kira · Il tuo spazio di lavoro"); font: pane.adjustedFont(appearance.uiFont, appearance.uiFont.pixelSize, Font.DemiBold) }
            }
            TextEdit {
                objectName: "appearancePreviewText"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                readOnly: true
                selectByMouse: true
                wrapMode: TextEdit.Wrap
                textFormat: TextEdit.RichText
                font: appearance.contentFont
                color: appearance.colors.text
                selectionColor: appearance.colors.selection
                selectedTextColor: appearance.colors.selectedText
                text: markdownRenderer.renderForAppearance(qsTr('Un tema per "le tue idee" -- con testo leggibile, **dettagli chiari** e codice.') + '\n\n```python\ndef saluta(nome):\n    return f"Ciao, {nome}!"\n```\n\n```diff\n- titolo = "Bozza"\n+ titolo = "Pronto"\n```', appearance.revision + localization.revision)
            }
        }
    }

    SectionHeading { text: qsTr("Caratteri") }
    Description { text: qsTr("Scegli tra i caratteri installati sul computer. Le dimensioni sono indipendenti e indicate in pixel.") }
    FontCard { title: qsTr("Interfaccia"); fontScope: "ui"; defaultLabel: qsTr("Carattere di sistema"); defaultFamily: "system"; maximumSize: 18 }
    FontCard { title: qsTr("Conversazione"); fontScope: "content"; defaultLabel: qsTr("Come l’interfaccia"); defaultFamily: "interface" }
    FontCard { title: qsTr("Codice"); fontScope: "code"; defaultLabel: qsTr("Monospazio di sistema"); defaultFamily: "monospace"; minimumSize: 10 }

    SectionHeading { text: qsTr("Dettagli dell’interfaccia") }
    GridLayout {
        Layout.fillWidth: true
        columns: pane.narrow ? 1 : 2
        columnSpacing: 18
        rowSpacing: 8
        Caption { text: qsTr("Riduci le animazioni") }
        KComboBox {
            objectName: "reduceMotionChoice"
            Layout.fillWidth: pane.narrow
            Layout.preferredWidth: 264
            model: [qsTr("Segui il sistema"), qsTr("Sempre"), qsTr("Mai")]
            currentIndex: ["system", "on", "off"].indexOf(appearance.values.motion)
            onActivated: appearance.setOption("motion", ["system", "on", "off"][currentIndex])
            Accessible.name: qsTr("Riduci le animazioni")
        }
    }
    Description {
        visible: appearance.values.motion === "system" && !appearance.motionSystemAvailable
        text: qsTr("Il sistema non espone una preferenza per le animazioni. Scegli Sempre per ridurle.")
    }
    CheckRow {
        title: qsTr("Barra laterale traslucida")
        note: qsTr("Lascia intravedere lo sfondo dell’app nella barra laterale.")
        option: "sidebar_translucent"
        controlName: "sidebarTranslucentChoice"
    }
    RowLayout {
        Layout.fillWidth: true
        Caption { text: qsTr("Contrasto degli elementi") }
        Caption { Layout.fillWidth: false; text: appearance.values.contrast + "%"; color: appearance.colors.muted }
    }
    Slider {
        objectName: "appearanceContrastSlider"
        readonly property int roundedValue: Math.round(value)
        Layout.fillWidth: true
        from: 0
        to: 100
        stepSize: 1
        value: appearance.values.contrast
        onMoved: appearance.setOption("contrast", roundedValue)
        Accessible.name: qsTr("Contrasto degli elementi, da zero a cento")
    }
    CheckRow {
        title: qsTr("Punteggiatura tipografica")
        note: qsTr("Solo nelle risposte formattate. Messaggi originali, codice e invio restano invariati.")
        option: "smart_punctuation"
        controlName: "smartPunctuationChoice"
    }
    CheckRow {
        title: qsTr("Conversazione a tutta larghezza")
        note: qsTr("Usa tutto lo spazio disponibile per i messaggi.")
        option: "full_width"
        controlName: "fullWidthChoice"
    }
    GridLayout {
        Layout.fillWidth: true
        columns: pane.narrow ? 1 : 2
        columnSpacing: 18
        rowSpacing: 8
        Caption { text: qsTr("Indicatori delle modifiche nel codice") }
        KComboBox {
            objectName: "diffIndicatorsChoice"
            Layout.fillWidth: pane.narrow
            Layout.preferredWidth: 264
            model: [qsTr("Colore e segni"), qsTr("Solo segni")]
            currentIndex: appearance.values.diff_indicators === "signs" ? 1 : 0
            onActivated: appearance.setOption("diff_indicators", currentIndex === 1 ? "signs" : "color")
            Accessible.name: qsTr("Indicatori delle modifiche nel codice")
        }
    }
    CheckRow {
        title: qsTr("Cursore a mano sui comandi")
        option: "pointer_cursors"
        controlName: "pointerCursorsChoice"
    }

    Flow {
        Layout.fillWidth: true
        spacing: 8
        KButton {
            objectName: "copyThemeButton"
            text: qsTr("Copia tema")
            onClicked: {
                appearance.copyTheme()
                pane.transferState = appearance.error ? "" : "copied"
            }
        }
        KButton {
            objectName: "exportThemeButton"
            text: qsTr("Esporta tema")
            onClicked: {
                themeDialog.importing = false
                themeJson.text = appearance.exportTheme()
                themeDialog.open()
            }
        }
        KButton {
            objectName: "importThemeButton"
            text: qsTr("Importa tema")
            onClicked: {
                themeDialog.importing = true
                themeJson.text = ""
                themeDialog.open()
            }
        }
        KButton {
            objectName: "resetAppearanceButton"
            text: qsTr("Ripristina aspetto")
            onClicked: {
                appearance.reset()
                pane.transferState = appearance.error ? "" : "reset"
            }
        }
    }
    Description {
        visible: pane.transferMessage.length > 0
        text: pane.transferMessage
        color: appearance.colors.accent
    }
    Description {
        objectName: "appearanceError"
        visible: appearance.error.length > 0
        text: appearance.error
        color: appearance.colors.danger
    }

    Dialog {
        id: themeDialog
        objectName: "themeJsonDialog"
        property bool importing: false
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(700, parent ? parent.width - 32 : 700)
        height: Math.min(540, parent ? parent.height - 40 : 540)
        modal: true
        title: importing ? qsTr("Importa tema") : qsTr("Esporta tema")
        background: Rectangle { color: appearance.colors.surface; radius: 12; border.color: appearance.colors.border }
        ColumnLayout {
            anchors.fill: parent
            spacing: 12
            Description {
                text: themeDialog.importing
                    ? qsTr("Incolla un tema JSON esportato da Kira. Verranno aggiornate solo le preferenze di aspetto.")
                    : qsTr("Copia il tema JSON per conservarlo in un file o condividerlo. La versione del formato è inclusa.")
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                TextArea {
                    id: themeJson
                    objectName: "themeJsonInput"
                    readOnly: !themeDialog.importing
                    selectByMouse: true
                    wrapMode: TextEdit.Wrap
                    textFormat: TextEdit.PlainText
                    font: appearance.codeFont
                    color: appearance.colors.text
                    placeholderText: qsTr("Incolla qui il tema JSON…")
                    Accessible.name: qsTr("Tema JSON")
                }
            }
            Description {
                visible: themeDialog.importing && appearance.error.length > 0
                text: appearance.error
                color: appearance.colors.danger
            }
            RowLayout {
                Layout.fillWidth: true
                KButton {
                    objectName: "applyImportedThemeButton"
                    visible: themeDialog.importing
                    enabled: themeJson.text.trim().length > 0
                    primary: true
                    text: qsTr("Importa")
                    onClicked: if (appearance.importTheme(themeJson.text)) {
                        pane.transferState = "imported"
                        themeDialog.close()
                    }
                }
                KButton {
                    visible: !themeDialog.importing
                    text: qsTr("Copia JSON")
                    primary: true
                    onClicked: {
                        appearance.copyTheme()
                        pane.transferState = appearance.error ? "" : "copied"
                        themeDialog.close()
                    }
                }
                Item { Layout.fillWidth: true }
                KButton { text: qsTr("Chiudi"); onClicked: themeDialog.close() }
            }
        }
    }
}
