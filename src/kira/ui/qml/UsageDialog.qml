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
    readonly property var telemetry: {
        localization.revision
        return metricsPresenter.presentTelemetry(
            workspace.usageData,
            typeof chatContext !== "undefined" ? chatContext.metricsContext : {},
            workspace.backend || "codex",
            workspace.selectedModel || "",
            workspace.tokenBudget || 0,
            workspace.costBudgetUsd || 0.0,
            typeof teams !== "undefined" ? teams.rawTelemetry : {}
        )
    }
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
                Rectangle {
                    objectName: "usageCostCard"
                    visible: !dialog.rawVisible && Boolean(dialog.telemetry && dialog.telemetry.cost_card)
                    Layout.fillWidth: true
                    implicitHeight: costCardCol.implicitHeight + 28
                    radius: 12; color: appearance.colors.surfaceAlt; border.color: appearance.colors.border
                    ColumnLayout {
                        id: costCardCol
                        anchors.left: parent.left; anchors.right: parent.right
                        anchors.top: parent.top; anchors.margins: 14
                        spacing: 9
                        ReadText {
                            Layout.fillWidth: true
                            text: dialog.telemetry.cost_card ? dialog.telemetry.cost_card.title : ""
                            font.pixelSize: appearance.uiFont.pixelSize + 3; font.bold: true
                            color: appearance.colors.accent
                        }
                        ReadText {
                            Layout.fillWidth: true
                            text: dialog.telemetry.cost_card ? dialog.telemetry.cost_card.note : ""
                            color: appearance.colors.muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1)
                        }
                        Repeater {
                            model: dialog.telemetry.cost_card ? dialog.telemetry.cost_card.rows : []
                            delegate: RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                ReadText { text: modelData.label; Layout.fillWidth: true; color: appearance.colors.text }
                                ReadText { text: modelData.value; Layout.preferredWidth: Math.min(220, scroll.availableWidth * 0.46); horizontalAlignment: Text.AlignRight; font.bold: true }
                            }
                        }
                    }
                }
                Rectangle {
                    objectName: "usagePromptCachingCard"
                    visible: !dialog.rawVisible && Boolean(dialog.telemetry && dialog.telemetry.caching_card)
                    Layout.fillWidth: true
                    implicitHeight: cachingCardCol.implicitHeight + 28
                    radius: 12; color: appearance.colors.surfaceAlt; border.color: appearance.colors.border
                    ColumnLayout {
                        id: cachingCardCol
                        anchors.left: parent.left; anchors.right: parent.right
                        anchors.top: parent.top; anchors.margins: 14
                        spacing: 9
                        RowLayout {
                            Layout.fillWidth: true
                            ReadText {
                                Layout.fillWidth: true
                                text: dialog.telemetry.caching_card ? dialog.telemetry.caching_card.title : ""
                                font.pixelSize: appearance.uiFont.pixelSize + 3; font.bold: true
                                color: appearance.colors.accent
                            }
                            ReadText {
                                text: dialog.telemetry.caching_card ? Math.round(Number(dialog.telemetry.caching_card.hit_rate_pct || 0)) + "% cache hit" : ""
                                color: appearance.colors.success; font.bold: true
                            }
                        }
                        ReadText {
                            Layout.fillWidth: true
                            text: dialog.telemetry.caching_card ? dialog.telemetry.caching_card.note : ""
                            color: appearance.colors.muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1)
                        }
                        Rectangle {
                            Layout.fillWidth: true; implicitHeight: 8; radius: 4
                            color: appearance.colors.surface; border.color: appearance.colors.border
                            Rectangle {
                                width: Math.max(0, Math.min(parent.width, Math.round(parent.width * Number(dialog.telemetry.caching_card ? dialog.telemetry.caching_card.hit_rate_pct || 0 : 0) / 100.0)))
                                height: parent.height; radius: 4
                                color: appearance.colors.success
                            }
                        }
                        Repeater {
                            model: dialog.telemetry.caching_card ? dialog.telemetry.caching_card.rows : []
                            delegate: RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                ReadText { text: modelData.label; Layout.fillWidth: true; color: appearance.colors.text }
                                ReadText { text: modelData.value; Layout.preferredWidth: Math.min(220, scroll.availableWidth * 0.46); horizontalAlignment: Text.AlignRight; font.bold: true }
                            }
                        }
                    }
                }
                Rectangle {
                    objectName: "usageBudgetCard"
                    visible: !dialog.rawVisible && Boolean(dialog.telemetry && dialog.telemetry.budget_card)
                    Layout.fillWidth: true
                    implicitHeight: budgetCardCol.implicitHeight + 28
                    radius: 12; color: appearance.colors.surfaceAlt
                    border.color: dialog.telemetry.budget_card && dialog.telemetry.budget_card.exceeded ? appearance.colors.danger : dialog.telemetry.budget_card && dialog.telemetry.budget_card.warning ? appearance.colors.warning : appearance.colors.border
                    ColumnLayout {
                        id: budgetCardCol
                        anchors.left: parent.left; anchors.right: parent.right
                        anchors.top: parent.top; anchors.margins: 14
                        spacing: 9
                        ReadText {
                            Layout.fillWidth: true
                            text: dialog.telemetry.budget_card ? dialog.telemetry.budget_card.title : ""
                            font.pixelSize: appearance.uiFont.pixelSize + 3; font.bold: true
                            color: dialog.telemetry.budget_card && dialog.telemetry.budget_card.exceeded ? appearance.colors.danger : appearance.colors.accent
                        }
                        ReadText {
                            Layout.fillWidth: true
                            text: dialog.telemetry.budget_card ? dialog.telemetry.budget_card.note : ""
                            color: appearance.colors.muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1)
                        }
                        Repeater {
                            model: dialog.telemetry.budget_card ? dialog.telemetry.budget_card.rows : []
                            delegate: RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                ReadText { text: modelData.label; Layout.fillWidth: true; color: appearance.colors.text }
                                ReadText { text: modelData.value; Layout.preferredWidth: Math.min(220, scroll.availableWidth * 0.46); horizontalAlignment: Text.AlignRight; font.bold: true }
                            }
                        }
                        Flow {
                            Layout.fillWidth: true; spacing: 6
                            visible: typeof workspace.setCostBudgetUsd === "function"
                            Text { font: appearance.uiFont; text: qsTr(" Soglia costo:"); textFormat: Text.PlainText; color: appearance.colors.muted; verticalAlignment: Text.AlignVCenter; height: 30 }
                            Repeater {
                                model: [
                                    { label: qsTr("Illimitato"), value: 0.0 },
                                    { label: "$0.50", value: 0.5 },
                                    { label: "$2.00", value: 2.0 },
                                    { label: "$10.00", value: 10.0 }
                                ]
                                delegate: KButton {
                                    required property var modelData
                                    required property int index
                                    objectName: "costBudgetPreset_" + index
                                    text: modelData.label; subtle: true
                                    checked: Math.abs(Number(workspace.costBudgetUsd || 0) - modelData.value) < 0.001
                                    onClicked: if (typeof workspace.setCostBudgetUsd === "function") workspace.setCostBudgetUsd(modelData.value)
                                }
                            }
                        }
                        Flow {
                            Layout.fillWidth: true; spacing: 6
                            visible: typeof workspace.setTokenBudget === "function"
                            Text { font: appearance.uiFont; text: qsTr(" Soglia token:"); textFormat: Text.PlainText; color: appearance.colors.muted; verticalAlignment: Text.AlignVCenter; height: 30 }
                            Repeater {
                                model: [
                                    { label: qsTr("Illimitato"), value: 0 },
                                    { label: "100k tok", value: 100000 },
                                    { label: "500k tok", value: 500000 },
                                    { label: "2M tok", value: 2000000 }
                                ]
                                delegate: KButton {
                                    required property var modelData
                                    required property int index
                                    objectName: "tokenBudgetPreset_" + index
                                    text: modelData.label; subtle: true
                                    checked: Number(workspace.tokenBudget || 0) === modelData.value
                                    onClicked: if (typeof workspace.setTokenBudget === "function") workspace.setTokenBudget(modelData.value)
                                }
                            }
                        }
                    }
                }
                Rectangle {
                    objectName: "usageTeamTimelineCard"
                    visible: !dialog.rawVisible && Boolean(dialog.telemetry && dialog.telemetry.team_timeline && (dialog.telemetry.team_timeline.spans || []).length > 0)
                    Layout.fillWidth: true
                    implicitHeight: timelineCardCol.implicitHeight + 28
                    radius: 12; color: appearance.colors.surfaceAlt; border.color: appearance.colors.border
                    ColumnLayout {
                        id: timelineCardCol
                        anchors.left: parent.left; anchors.right: parent.right
                        anchors.top: parent.top; anchors.margins: 14
                        spacing: 9
                        ReadText {
                            Layout.fillWidth: true
                            text: dialog.telemetry.team_timeline ? dialog.telemetry.team_timeline.title : ""
                            font.pixelSize: appearance.uiFont.pixelSize + 3; font.bold: true
                            color: appearance.colors.accent
                        }
                        ReadText {
                            Layout.fillWidth: true
                            text: dialog.telemetry.team_timeline ? dialog.telemetry.team_timeline.note : ""
                            color: appearance.colors.muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 1)
                        }
                        Repeater {
                            model: dialog.telemetry.team_timeline ? dialog.telemetry.team_timeline.summary_rows : []
                            delegate: RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                ReadText { text: modelData.label; Layout.fillWidth: true; color: appearance.colors.text }
                                ReadText { text: modelData.value; Layout.preferredWidth: Math.min(220, scroll.availableWidth * 0.46); horizontalAlignment: Text.AlignRight; font.bold: true }
                            }
                        }
                        Repeater {
                            model: dialog.telemetry.team_timeline ? dialog.telemetry.team_timeline.spans : []
                            delegate: ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true; spacing: 3
                                RowLayout {
                                    Layout.fillWidth: true
                                    ReadText { text: "R" + modelData.round_index + " · " + modelData.member_name + " (" + modelData.title + ")"; color: appearance.colors.text; Layout.fillWidth: true }
                                    ReadText { text: modelData.duration_label + " · " + modelData.tokens_label + " tok · " + modelData.cost_label; color: appearance.colors.muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
                                }
                                Rectangle {
                                    Layout.fillWidth: true; implicitHeight: 8; radius: 4
                                    color: appearance.colors.surface; border.color: appearance.colors.border
                                    Rectangle {
                                        x: Math.round(parent.width * Number(modelData.start_pct || 0) / 100.0)
                                        width: Math.max(6, Math.round(parent.width * Number(modelData.width_pct || 4) / 100.0))
                                        height: parent.height; radius: 4
                                        color: modelData.state === "failed" ? appearance.colors.danger : modelData.state === "running" ? appearance.colors.warning : appearance.colors.accent
                                    }
                                }
                            }
                        }
                    }
                }
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
