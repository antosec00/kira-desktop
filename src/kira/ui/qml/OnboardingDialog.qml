import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; font.pixelSize: appearance.uiFont.pixelSize
    id: dialog
    objectName: "onboardingDialog"
    title: qsTr("Benvenuto in Kira %1").arg(workspace.appVersion)
    modal: true; anchors.centerIn: parent
    width: Math.min(700, parent.width - 50); height: Math.min(660, parent.height - 50)
    signal createRequested()
    signal modelsRequested()
    background: Rectangle { color: appearance.colors.surface; radius: 12; border.color: appearance.colors.border }
    ColumnLayout {
        anchors.fill: parent; spacing: 16
        ScrollView {
            id: guide; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
            ColumnLayout {
                width: guide.availableWidth; spacing: 18
                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("Un progetto. Un punto da cui ripartire."); color: appearance.colors.text; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: appearance.uiFont.pixelSize + 10 }
                ReadText { text: qsTr("Kira Desktop organizza progetti, conversazioni e prove in un’applicazione desktop indipendente. Il provider che scegli esegue il lavoro AI."); Layout.fillWidth: true; color: appearance.colors.muted }
                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("1  Scegli una cartella e i permessi"); color: appearance.colors.accent; font.pixelSize: appearance.uiFont.pixelSize + 3 }
                ReadText { text: qsTr("Crea un progetto nella tua cartella di lavoro. Conversazioni, note e riferimenti restano associati al progetto."); Layout.fillWidth: true }
                KButton { text: qsTr("Crea progetto"); enabled: workspace.connected; onClicked: { dialog.close(); dialog.createRequested() } }
                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("2  Collega il provider e scegli il modello"); color: appearance.colors.accent; font.pixelSize: appearance.uiFont.pixelSize + 3; Layout.fillWidth: true; wrapMode: Text.Wrap }
                ReadText { text: qsTr("In Modelli scegli il provider, completa l’accesso o la configurazione che richiede, aggiorna il catalogo e scegli modello e ragionamento. Un turno completato verifica l’accesso effettivo. Kira non installa i provider e non scarica modelli."); Layout.fillWidth: true }
                KButton { objectName: "onboardingModelsButton"; text: qsTr("Apri Modelli"); onClicked: { dialog.close(); dialog.modelsRequested() } }
                Text { font.family: appearance.uiFont.family; font.styleName: appearance.uiFont.styleName; font.weight: appearance.uiFont.weight; font.italic: appearance.uiFont.italic; textFormat: Text.PlainText; text: qsTr("3  Riprendi e verifica"); color: appearance.colors.accent; font.pixelSize: appearance.uiFont.pixelSize + 3 }
                ReadText { text: qsTr("Riprendi lavoro mostra gli esiti, conserva il prossimo passo e apre i tuoi strumenti. Collega i file da ricontrollare e usa Ctrl+K per progetti e azioni. Le operazioni interrotte non vengono ripetute automaticamente."); Layout.fillWidth: true }
                ReadText { text: qsTr("Le azioni desktop sono richieste esplicite. Per controllare il collegamento al servizio e gli strumenti disponibili, apri Diagnostica locale nelle Impostazioni."); Layout.fillWidth: true; color: appearance.colors.muted; font.pixelSize: Math.max(8, appearance.uiFont.pixelSize - 2) }
            }
        }
        RowLayout {
            Item { Layout.fillWidth: true }
            KButton { text: qsTr("Più tardi"); subtle: true; onClicked: dialog.close() }
            KButton { objectName: "completeOnboardingButton"; text: qsTr("Ho capito"); primary: true; onClicked: { workspace.completeOnboarding(true); dialog.close() } }
        }
    }
}
