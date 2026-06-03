import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE_CLASS = "AutoCachedPreview";
const ROUTE_BASE = "/orion4d_auto_cached_preview";

function getNodeId(node) {
    return String(node?.id ?? "node");
}

function getStatusWidget(node) {
    if (node.__orion4dStatusWidget) return node.__orion4dStatusWidget;
    const widget = node.widgets?.find((item) => item.name === "clipboard_status");
    if (widget) node.__orion4dStatusWidget = widget;
    return widget || null;
}

function setStatus(node, message) {
    const widget = getStatusWidget(node);
    if (widget) widget.value = message;
    markCanvasDirty(node);
}

function markCanvasDirty(node) {
    try { node?.setDirtyCanvas?.(true, true); } catch (_) {}
    try { app.graph?.setDirtyCanvas?.(true, true); } catch (_) {}
    try { app.canvas?.setDirty?.(true, true); } catch (_) {}
    try { app.graph?.change?.(); } catch (_) {}
}

function clearNativePreviewState(node) {
    // Classic LiteGraph preview state.
    node.images = [];
    node.imgs = [];
    node.imageIndex = null;
    node.overIndex = null;

    // Some recent frontend builds keep transient preview state in different
    // places. These assignments are deliberately defensive/no-op when absent.
    if (node.widgets_values && Array.isArray(node.widgets_values)) {
        node.widgets_values = [...node.widgets_values];
    }

    markCanvasDirty(node);
}

function emitExecutedPreview(node, uiOutput) {
    const nodeId = getNodeId(node);
    const output = uiOutput || { images: [], text: [] };

    clearNativePreviewState(node);

    // Classic frontend path: this is what normal websocket execution eventually
    // calls for image-preview nodes.
    try {
        if (typeof node.onExecuted === "function") {
            node.onExecuted(output);
        }
    } catch (error) {
        console.warn("[Orion4D AutoCachedPreview] node.onExecuted refresh failed:", error);
    }

    // Nodes 2.0 path: it is Vue-based and reacts better to the normal ComfyUI
    // 'executed' event than to LiteGraph canvas drawing hooks.
    try {
        const event = new CustomEvent("executed", {
            detail: {
                node: nodeId,
                output,
            },
        });
        api.dispatchEvent?.(event);
    } catch (error) {
        console.warn("[Orion4D AutoCachedPreview] executed event dispatch failed:", error);
    }

    // Empty outputs must really empty the node; some frontends ignore an empty
    // images array in onExecuted and keep the old thumbnail alive.
    if (!Array.isArray(output.images) || output.images.length === 0) {
        clearNativePreviewState(node);
    }

    markCanvasDirty(node);
}

async function readClipboardImageBlob() {
    if (!navigator.clipboard?.read) {
        throw new Error("Clipboard image read is not supported by this browser. Use Chrome/Edge on localhost or HTTPS.");
    }

    const items = await navigator.clipboard.read();
    for (const item of items) {
        const imageType = item.types.find((type) => type.startsWith("image/"));
        if (imageType) {
            return await item.getType(imageType);
        }
    }

    throw new Error("No image found in the clipboard.");
}

async function pasteClipboardImage(node) {
    setStatus(node, "Reading clipboard...");

    const blob = await readClipboardImageBlob();
    const body = new FormData();
    body.append("node_id", getNodeId(node));
    body.append("image", blob, `clipboard_${getNodeId(node)}.png`);

    const response = await api.fetchApi(`${ROUTE_BASE}/paste`, {
        method: "POST",
        body,
    });
    const data = await response.json();

    if (!response.ok || !data.ok) {
        throw new Error(data?.error || "Clipboard image paste failed.");
    }

    const imageInfo = Array.isArray(data?.ui?.images) ? data.ui.images[0] : null;
    if (!imageInfo) {
        throw new Error("The server did not return a preview image.");
    }

    emitExecutedPreview(node, data.ui);

    const width = data?.meta?.width;
    const height = data?.meta?.height;
    setStatus(node, width && height ? `Pasted image: ${width} × ${height}` : "Pasted image cached");
}

async function clearCache(node) {
    setStatus(node, "Clearing cache...");

    const body = new FormData();
    body.append("node_id", getNodeId(node));

    const response = await api.fetchApi(`${ROUTE_BASE}/clear`, {
        method: "POST",
        body,
    });
    const data = await response.json();

    if (!response.ok || !data.ok) {
        throw new Error(data?.error || "Cache clear failed.");
    }

    emitExecutedPreview(node, data.ui || { images: [], text: ["Cache cleared"] });
    setStatus(node, "Cache cleared");
}

function hasWidget(node, name) {
    return Boolean(node.widgets?.some((widget) => widget.name === name));
}

function addPasteControls(node) {
    if (node.__orion4dPasteControlsAdded) return;
    node.__orion4dPasteControlsAdded = true;

    if (!hasWidget(node, "Paste image")) {
        const pasteWidget = node.addWidget("button", "Paste image", null, async () => {
            try {
                await pasteClipboardImage(node);
            } catch (error) {
                const message = error?.message || String(error);
                setStatus(node, `Paste failed: ${message}`);
                console.error("[Orion4D AutoCachedPreview] Paste image failed:", error);
                alert(`Paste image failed:\n${message}`);
            }
        });
        pasteWidget.serialize = false;
    }

    if (!hasWidget(node, "Clear cache")) {
        const clearWidget = node.addWidget("button", "Clear cache", null, async () => {
            try {
                await clearCache(node);
            } catch (error) {
                const message = error?.message || String(error);
                setStatus(node, `Clear failed: ${message}`);
                console.error("[Orion4D AutoCachedPreview] Clear cache failed:", error);
                alert(`Clear cache failed:\n${message}`);
            }
        });
        clearWidget.serialize = false;
    }

    if (!hasWidget(node, "clipboard_status")) {
        const statusWidget = node.addWidget("text", "clipboard_status", "Clipboard cache ready", () => {});
        statusWidget.serialize = false;
        statusWidget.disabled = true;
        node.__orion4dStatusWidget = statusWidget;
    } else {
        getStatusWidget(node);
    }
}

app.registerExtension({
    name: "orion4d.auto_cached_preview.clipboard_buttons",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData?.name !== NODE_CLASS) return;

        const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = originalOnNodeCreated?.apply(this, arguments);
            addPasteControls(this);
            return result;
        };

        const originalOnExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (output) {
            // Clear first so an empty image list after Clear Cache does not leave
            // an old native thumbnail behind.
            if (!output?.images?.length) {
                clearNativePreviewState(this);
            }
            const result = originalOnExecuted?.apply(this, arguments);
            markCanvasDirty(this);
            return result;
        };
    },
});
