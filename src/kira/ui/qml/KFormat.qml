import QtQuick

// Shared presentation rules for views that show provider identifiers or
// service timestamps. Unknown values stay literal; nothing here changes state.
QtObject {
    // The same names the provider selectors show.
    function providerLabel(backend) {
        switch (String(backend || "")) {
        case "ollama": return "Ollama"
        case "codex": return "Codex"
        case "openai_chatgpt": return qsTr("OpenAI diretto · ChatGPT")
        case "deepseek_account": return qsTr("DeepSeek Account")
        case "deepseek_web": return qsTr("DeepSeek Web · sperimentale")
        case "claude_code": return qsTr("Claude Code · uso personale")
        case "google_cloud": return qsTr("Google Cloud · Vertex AI")
        case "openrouter": return qsTr("OpenRouter")
        case "kira_cloud": return qsTr("Kira Cloud")
        }
        return String(backend || "")
    }

    // Ollama has its own thinking choice and DeepSeek Web declares no
    // reasoning levels: every view hides the reasoning level for both.
    function offersEffort(backend) {
        return ["ollama", "deepseek_web"].indexOf(String(backend || "")) < 0
    }

    // Model lists. Kinds, orders and the special groups are named here; a maker keeps the
    // name the catalog gives it.
    function modelKindLabel(kind) {
        switch (String(kind || "")) {
        case "text": return qsTr("Testo")
        case "image": return qsTr("Immagini")
        }
        return qsTr("Tutti")
    }
    function modelSortLabel(sort) {
        switch (String(sort || "")) {
        case "price_low": return qsTr("Più economici")
        case "price_high": return qsTr("Più costosi")
        case "capability": return qsTr("Più capaci")
        }
        return qsTr("Consigliati")
    }
    function modelGroupLabel(group) {
        switch (String(group || "")) {
        case "__quick__": return qsTr("Scelte rapide")
        case "__text__": return qsTr("Modelli di testo")
        case "__image__": return qsTr("Modelli per immagini")
        }
        return String(group || "")
    }
    // Numbers follow the interface language, like the amounts the account panel shows.
    function uiLocale() {
        return Qt.locale(typeof localization !== "undefined" ? localization.language : "")
    }
    function money(value, currency, digits) {
        return Number(value).toLocaleString(uiLocale(), "f", digits) + " " + (currency === "USD" ? "$" : "€")
    }
    // The price as the catalog states it: per image for pictures, read / written per 1M tokens for text.
    function modelPrice(row) {
        const pricing = row && row.pricing ? row.pricing : null
        if (!pricing) return ""
        if (pricing.per_image > 0) return qsTr("da %1 a immagine").arg(money(pricing.per_image, pricing.currency, pricing.per_image < 0.1 ? 3 : 2))
        if (!(pricing.input_per_1m > 0) && !(pricing.output_per_1m > 0)) return ""
        return qsTr("%1 / %2 per 1M").arg(Number(pricing.input_per_1m).toLocaleString(uiLocale(), "f", 2)).arg(money(pricing.output_per_1m, pricing.currency, 2))
    }
    function modelScore(row) {
        const scores = row && row.scores ? row.scores : null
        const value = scores ? Number(row.kind === "image" ? scores.image : scores.intelligence) : 0
        return value > 0 ? value : 0
    }
    // One line beside a model's name in a list: who makes it, how capable, what it costs.
    function modelDetail(row) {
        if (!row) return ""
        const parts = []
        const group = modelGroupLabel(row.group)
        if (group.length > 0) parts.push(group)
        const score = modelScore(row)
        if (score > 0) parts.push(qsTr("indice %1").arg(Number(score).toLocaleString(uiLocale(), "f", 0)))
        const price = modelPrice(row)
        if (price.length > 0) parts.push(price)
        return parts.join("  ·  ")
    }

    // Service timestamps are ISO 8601 in UTC; show them in the interface
    // language and the computer's time zone, like the Python views do.
    function timestamp(value) {
        const text = String(value || "")
        const moment = new Date(text)
        if (!text.length || isNaN(moment.getTime())) return text
        const language = typeof localization !== "undefined" ? localization.language : ""
        return moment.toLocaleString(Qt.locale(language), "d MMM yyyy, HH:mm")
    }

    function relativeTime(value) {
        const text = String(value || "")
        const moment = new Date(text)
        if (!text.length || isNaN(moment.getTime())) return ""
        const minutes = Math.max(1, Math.floor((Date.now() - moment.getTime()) / 60000))
        if (minutes < 60) return minutes + "m"
        const hours = Math.floor(minutes / 60)
        if (hours < 24) return hours + "h"
        const language = typeof localization !== "undefined" ? localization.language : "it"
        const days = Math.floor(hours / 24)
        if (days < 30) return days + (language === "en" ? "d" : "g")
        const months = Math.floor(days / 30)
        if (months < 12) return months + "mo"
        return Math.floor(days / 365) + (language === "en" ? "y" : "a")
    }
}
