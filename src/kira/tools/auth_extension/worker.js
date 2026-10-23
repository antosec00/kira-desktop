// No content scripts, page bindings, external messages, network or credential storage.
// Only the owned browser worker's CDP session calls this private interface.
const api = chrome.webAuthenticationProxy;
let queued = [];
let waiter = null;
let active = null;
let attached = false;
const unavailable = {name: "NotAllowedError", message: "Authentication is not available"};

function reject(kind, requestId, error = unavailable) {
    const method = kind === "create" ? "completeCreateRequest" : "completeGetRequest";
    api[method]({requestId, error}).catch(() => {});
}

function deliver(item) {
    if (waiter) {
        const resolve = waiter;
        waiter = null;
        resolve(item);
    } else {
        queued.push(item);
    }
}

function request(kind, detail) {
    if (!attached || active !== null || queued.length >= 8 ||
            typeof detail.requestDetailsJson !== "string" ||
            detail.requestDetailsJson.length > 262144) {
        reject(kind, detail.requestId);
        return;
    }
    active = {kind, requestId: detail.requestId};
    deliver({kind, detail});
}

api.onCreateRequest.addListener(detail => request("create", detail));
api.onGetRequest.addListener(detail => request("get", detail));
api.onIsUvpaaRequest.addListener(detail => {
    if (!attached || queued.length >= 7) {
        api.completeIsUvpaaRequest({requestId: detail.requestId, isUvpaa: false}).catch(() => {});
    } else {
        deliver({kind: "uvpaa", detail});
    }
});
api.onRequestCanceled.addListener(requestId => {
    if (!active || active.requestId !== requestId) return;
    active = null;
    queued = queued.filter(item => item.detail.requestId !== requestId);
    deliver({kind: "cancel", detail: {requestId}});
});

globalThis.kiraAuthentication = Object.freeze({
    attach: async () => {
        await api.attach();
        attached = true;
    },
    next: () => {
        if (queued.length) return Promise.resolve(queued.shift());
        if (waiter) return Promise.reject(new Error("Authentication receiver already pending"));
        return new Promise(resolve => { waiter = resolve; });
    },
    complete: async value => {
        const {kind, requestId} = value;
        if (kind === "uvpaa") {
            await api.completeIsUvpaaRequest({requestId, isUvpaa: value.isUvpaa === true});
            return;
        }
        if (!active || active.kind !== kind || active.requestId !== requestId) {
            throw new Error("Authentication request no longer active");
        }
        active = null;
        const details = {requestId};
        if (value.error) details.error = value.error;
        else if (typeof value.responseJson === "string" && value.responseJson.length <= 262144) {
            details.responseJson = value.responseJson;
        } else {
            details.error = unavailable;
        }
        await api[kind === "create" ? "completeCreateRequest" : "completeGetRequest"](details);
    },
    detach: async () => {
        attached = false;
        active = null;
        queued = [];
        await api.detach();
    }
});
