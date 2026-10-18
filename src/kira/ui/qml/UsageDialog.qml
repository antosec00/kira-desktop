import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

KiraPanel {
    font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
    id: dialog
    objectName: "usageDialog"
    title: qsTr("Uso e risorse")
    fallbackWidth: 780
    fallbackHeight: 760
    property bool rawVisible: false
    signal contextRequested()
    onOpened: { accountUsage.open(); if (typeof chatContext !== "undefined" && chatContext.available) chatContext.refresh() }
    onClosed: accountUsage.hide()
    readonly property var summary: { localization.revision; return metricsPresenter.present(workspace.usageData, workspace.resourcesData, typeof chatContext !== "undefined" ? chatContext.metricsContext : {}) }
    readonly property string originalText: qsTr("UTILIZZO DELL’ACCOUNT") + "\n" + accountUsage.rawText + "\n\n" + qsTr("USO DEL PROVIDER") + "\n" + workspace.usageText + "\n\n" + qsTr("RISORSE OSSERVATE") + "\n" + workspace.resourcesText
    footer: RowLayout {
        spacing: 8
        Item { Layout.fillWidth: true }
        KButton {
            objectName: "usageClose"; text: qsTr("Chiudi")
            Layout.rightMargin: 16; Layout.bottomMargin: 12
            onClicked: dialog.close()
        }
    }
    contentItem: ColumnLayout {
        spacing: 12
        Flow {
            Layout.fillWidth: true
            spacing: 6
            KButton {
                objectName: "usageRawToggle"
                text: dialog.rawVisible ? qsTr("Riepilogo") : qsTr("Dati originali")
                onClicked: dialog.rawVisible = !dialog.rawVisible
            }
            KButton {
                objectName: "usageContext"; text: qsTr("Contesto e memoria")
                visible: typeof chatContext !== "undefined" && chatContext.available
                onClicked: { dialog.close(); dialog.contextRequested() }
            }
            KButton {
                objectName: "usageRefresh"; text: qsTr("Aggiorna risorse")
                enabled: workspace.connected
                onClicked: workspace.refreshResources()
            }
            KButton {
                objectName: "usageCopy"; text: qsTr("Copia"); visible: dialog.rawVisible
                onClicked: workspace.copy(dialog.originalText)
            }
        }
        ScrollView {
            id: scroll
            objectName: "usageScroll"
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            rightPadding: 20
            ScrollBar.vertical: KScrollBar {
                objectName: "usageScrollBar"
                parent: scroll
                orientation: Qt.Vertical
                x: scroll.width - width
                y: 0
                height: scroll.height
            }
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: scroll.availableWidth
                spacing: 12
                ReadText { color: appearance.colors.codeText;
                    objectName: "usageRawText"
                    visible: dialog.rawVisible
                    Layout.fillWidth: true
                    text: dialog.originalText
                    font.family: appearance.codeFont.family; font.styleName: appearance.codeFont.styleName; font.weight: appearance.codeFont.weight; font.italic: appearance.codeFont.italic; font.pixelSize: appearance.codeFont.pixelSize
                }
                AccountUsageCard { Layout.fillWidth: true; visible: !dialog.rawVisible }
                Repeater {
                    model: dialog.rawVisible ? [] : dialog.summary.cards
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        objectName: "usageCard_" + index
                        Layout.fillWidth: true
                        implicitHeight: cardColumn.implicitHeight + 28
                        radius: 12; color: appearance.colors.surfaceAlt; border.color: appearance.colors.border
                        ColumnLayout {
                            id: cardColumn
                            anchors.left: parent.left; anchors.right: parent.right
                            anchors.top: parent.top; anchors.margins: 14
                            spacing: 9
                            ReadText {
                                Layout.fillWidth: true
                                text: modelData.title; font.pixelSize: appearance.uiFont.pixelSize + 3; font.bold: true
                                color: appearance.colors.accent
                            }
                            ReadText {
                                Layout.fillWidth: true
                                text: modelData.note; color: appearance.colors.muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1)
                            }
                            Repeater {
                                model: modelData.rows
                                delegate: RowLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    ReadText { text: modelData.label; Layout.fillWidth: true; color: appearance.colors.text }
                                    ReadText { text: modelData.value; Layout.preferredWidth: Math.min(180, scroll.availableWidth * 0.42); horizontalAlignment: Text.AlignRight; font.bold: true }
                                }
                            }
                        }
                    }
                }
                ReadText {
                    visible: !dialog.rawVisible
                    Layout.fillWidth: true
                    text: dialog.summary.note; color: appearance.colors.muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1)
                }
            }
        }
    }
}
