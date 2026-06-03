import json
import os
import re
import time
from glob import glob
from typing import Optional, Tuple

import numpy as np
from PIL import Image, ImageOps, PngImagePlugin
import torch

import folder_paths

try:
    from aiohttp import web
    from server import PromptServer
except Exception:
    web = None
    PromptServer = None


CACHE_SUBFOLDER = "AutoCachedPreview"
MAX_CLIPBOARD_IMAGE_BYTES = 64 * 1024 * 1024


def _safe_id(value) -> str:
    value = str(value) if value is not None else "node"
    value = re.sub(r"[^a-zA-Z0-9_\-]", "_", value)
    return value or "node"


def _cache_root() -> str:
    root = os.path.join(folder_paths.get_temp_directory(), CACHE_SUBFOLDER)
    os.makedirs(root, exist_ok=True)
    return root


def _node_paths_for_id(unique_id: str) -> Tuple[str, str, str]:
    root = _cache_root()
    safe_uid = _safe_id(unique_id)
    tensor_path = os.path.join(root, f"{safe_uid}_cache.pt")
    meta_path = os.path.join(root, f"{safe_uid}_meta.json")
    image_prefix = f"{safe_uid}_preview"
    return tensor_path, meta_path, image_prefix


def _cache_fingerprint(unique_id: str):
    """Return a stable-but-changing value for ComfyUI execution cache invalidation.

    The clipboard paste/clear buttons alter files outside the normal workflow inputs.
    Without IS_CHANGED, ComfyUI may reuse the old node output because the graph values
    have not changed. This fingerprint makes the next Queue Prompt see the node as
    changed whenever its node-local cache changes.
    """
    tensor_path, meta_path, image_prefix = _node_paths_for_id(unique_id)

    items = []
    for path in (tensor_path, meta_path):
        if os.path.exists(path):
            stat = os.stat(path)
            items.append((os.path.basename(path), int(stat.st_mtime_ns), int(stat.st_size)))
        else:
            items.append((os.path.basename(path), "missing", 0))

    preview_pattern = os.path.join(_cache_root(), f"{image_prefix}_*.png")
    previews = []
    for path in sorted(glob(preview_pattern)):
        if os.path.isfile(path):
            stat = os.stat(path)
            previews.append((os.path.basename(path), int(stat.st_mtime_ns), int(stat.st_size)))
    items.append(("previews", tuple(previews[-12:])))

    return tuple(items)


def _tensor_to_pil(image_tensor: torch.Tensor) -> Image.Image:
    arr = image_tensor.detach().cpu().numpy()
    arr = np.clip(arr, 0.0, 1.0)

    if arr.ndim == 2:
        arr = (arr * 255.0).round().astype(np.uint8)
        return Image.fromarray(arr, mode="L")

    if arr.shape[-1] == 1:
        arr = (arr[..., 0] * 255.0).round().astype(np.uint8)
        return Image.fromarray(arr, mode="L")

    if arr.shape[-1] == 4:
        arr = (arr * 255.0).round().astype(np.uint8)
        return Image.fromarray(arr, mode="RGBA")

    arr = (arr[..., :3] * 255.0).round().astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def _pil_to_image_tensor(pil_img: Image.Image) -> torch.Tensor:
    if pil_img.mode not in ("RGB", "RGBA", "L"):
        pil_img = pil_img.convert("RGBA")

    arr = np.array(pil_img).astype(np.float32) / 255.0
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    elif arr.shape[-1] == 4:
        arr = arr[..., :3]
    return torch.from_numpy(arr).unsqueeze(0)


def _pil_alpha_to_mask_tensor(pil_img: Image.Image) -> torch.Tensor:
    if "A" in pil_img.getbands():
        alpha = np.array(pil_img.getchannel("A")).astype(np.float32) / 255.0
        mask = 1.0 - alpha
    else:
        w, h = pil_img.size
        mask = np.zeros((h, w), dtype=np.float32)
    return torch.from_numpy(mask).unsqueeze(0)


def _json_error(message: str, status: int = 400):
    if web is None:
        raise RuntimeError(message)
    return web.json_response({"ok": False, "error": message}, status=status)


def _send_executed_ui_update(node_id, ui_output):
    """Push a normal ComfyUI 'executed' UI update for both classic nodes and Nodes 2.0.

    Paste/Clear are HTTP route actions, not workflow executions. Broadcasting the same
    message shape used by normal node execution lets the frontend refresh native image
    previews without relying only on LiteGraph canvas hooks.
    """
    if PromptServer is None:
        return

    try:
        PromptServer.instance.send_sync(
            "executed",
            {
                "node": str(node_id),
                "output": ui_output,
            },
        )
    except Exception:
        # UI refresh is best-effort; cache persistence must not fail because a client
        # cannot receive the websocket notification.
        pass


class AutoCachedPreview:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "fallback_to_cache": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "image": ("IMAGE",),
                "mask": ("MASK",),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    FUNCTION = "run"
    CATEGORY = "Orion4D_image-preview"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, fallback_to_cache=True, image=None, mask=None, unique_id=None, **kwargs):
        # The clipboard cache is an external file dependency. Returning its file
        # fingerprint prevents ComfyUI from reusing stale outputs after Paste/Clear.
        return (
            bool(fallback_to_cache),
            _safe_id(unique_id),
            _cache_fingerprint(unique_id),
        )

    def _node_paths(self, unique_id: str) -> Tuple[str, str, str]:
        return _node_paths_for_id(unique_id)

    def _default_mask_for_image(self, image: torch.Tensor) -> torch.Tensor:
        if image is None:
            raise ValueError("Cannot build a default mask without an image.")

        if image.ndim != 4:
            raise ValueError(f"Unexpected image tensor shape: {tuple(image.shape)}")

        batch, height, width, channels = image.shape
        if channels >= 4:
            alpha = image[..., 3]
            return 1.0 - alpha
        return torch.zeros((batch, height, width), dtype=image.dtype, device=image.device)

    def _normalize_mask(self, image: torch.Tensor, mask: Optional[torch.Tensor]) -> torch.Tensor:
        if mask is None:
            return self._default_mask_for_image(image)

        if mask.ndim == 2:
            mask = mask.unsqueeze(0)

        if image is not None and mask.shape[0] == 1 and image.shape[0] > 1:
            mask = mask.repeat(image.shape[0], 1, 1)

        return mask

    def _save_preview_images(self, image: torch.Tensor, unique_id: str, prompt=None, extra_pnginfo=None):
        root = _cache_root()
        _, _, image_prefix = self._node_paths(unique_id)
        preview_items = []

        # Versioned names avoid stale frontend/browser previews when the tensor was
        # updated by an external cache operation.
        version = time.time_ns()

        for idx in range(image.shape[0]):
            pil_img = _tensor_to_pil(image[idx])
            filename = f"{image_prefix}_{version}_{idx:03d}.png"
            path = os.path.join(root, filename)
            self._save_pil_preview(pil_img, path, prompt=prompt, extra_pnginfo=extra_pnginfo)
            preview_items.append({"filename": filename, "subfolder": CACHE_SUBFOLDER, "type": "temp"})

        return preview_items

    def _save_pil_preview(self, pil_img: Image.Image, path: str, prompt=None, extra_pnginfo=None):
        pnginfo = PngImagePlugin.PngInfo()
        if prompt is not None:
            pnginfo.add_text("prompt", json.dumps(prompt))
        if extra_pnginfo is not None:
            for key, value in extra_pnginfo.items():
                try:
                    pnginfo.add_text(key, json.dumps(value))
                except Exception:
                    pass

        pil_img.save(path, pnginfo=pnginfo, compress_level=4)

    def _save_clipboard_preview_image(self, pil_img: Image.Image, unique_id: str):
        root = _cache_root()
        _, _, image_prefix = self._node_paths(unique_id)

        version = time.time_ns()
        filename = f"{image_prefix}_clipboard_{version}.png"
        path = os.path.join(root, filename)
        self._save_pil_preview(pil_img, path)
        return [{"filename": filename, "subfolder": CACHE_SUBFOLDER, "type": "temp"}]

    def _save_cache(self, image: torch.Tensor, mask: torch.Tensor, unique_id: str, source: str = "live"):
        tensor_path, meta_path, _ = self._node_paths(unique_id)

        payload = {
            "image": image.detach().cpu(),
            "mask": mask.detach().cpu(),
        }
        torch.save(payload, tensor_path)

        meta = {
            "unique_id": str(unique_id),
            "source": source,
            "revision": time.time_ns(),
            "batch": int(image.shape[0]),
            "height": int(image.shape[1]),
            "width": int(image.shape[2]),
            "channels": int(image.shape[3]),
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

    def _load_cache(self, unique_id: str):
        tensor_path, _, _ = self._node_paths(unique_id)
        if not os.path.exists(tensor_path):
            return None, None

        payload = torch.load(tensor_path, map_location="cpu")
        image = payload.get("image")
        mask = payload.get("mask")
        if image is None or mask is None:
            return None, None
        return image, mask

    def _clear_node_cache(self, unique_id: str) -> int:
        tensor_path, meta_path, image_prefix = self._node_paths(unique_id)
        removed = 0

        for path in [tensor_path, meta_path]:
            if os.path.exists(path):
                os.remove(path)
                removed += 1

        preview_pattern = os.path.join(_cache_root(), f"{image_prefix}_*.png")
        for path in glob(preview_pattern):
            if os.path.isfile(path):
                os.remove(path)
                removed += 1

        return removed

    def _paste_pil_to_cache(self, pil_img: Image.Image, unique_id: str):
        pil_img = ImageOps.exif_transpose(pil_img)
        if pil_img.mode not in ("RGB", "RGBA"):
            pil_img = pil_img.convert("RGBA")

        image = _pil_to_image_tensor(pil_img)
        mask = _pil_alpha_to_mask_tensor(pil_img)
        self._save_cache(image, mask, unique_id, source="clipboard")
        ui_images = self._save_clipboard_preview_image(pil_img, unique_id)

        return {
            "ui": {
                "images": ui_images,
                "text": [f"AutoCachedPreview [{_safe_id(unique_id)}] source: clipboard"],
            },
            "meta": {
                "width": int(pil_img.size[0]),
                "height": int(pil_img.size[1]),
                "mode": pil_img.mode,
                "revision": time.time_ns(),
            },
        }

    def run(self, fallback_to_cache=True, image=None, mask=None, unique_id=None, prompt=None, extra_pnginfo=None):
        used_cache = False

        if image is not None:
            out_image = image
            out_mask = self._normalize_mask(out_image, mask)
            self._save_cache(out_image, out_mask, unique_id, source="live")
        else:
            if not fallback_to_cache:
                raise Exception("Auto Cached Preview: no input image connected and fallback_to_cache is disabled.")

            cached_image, cached_mask = self._load_cache(unique_id)
            if cached_image is None or cached_mask is None:
                raise Exception("Auto Cached Preview: no cached image available for this node yet.")

            out_image = cached_image
            out_mask = cached_mask
            used_cache = True

        ui_images = self._save_preview_images(out_image, unique_id, prompt=prompt, extra_pnginfo=extra_pnginfo)

        ui_message = "cached" if used_cache else "live"
        return {
            "ui": {
                "images": ui_images,
                "text": [f"AutoCachedPreview [{_safe_id(unique_id)}] source: {ui_message}"],
            },
            "result": (out_image, out_mask),
        }


_NODE_HELPER = AutoCachedPreview()


if PromptServer is not None and web is not None:
    routes = PromptServer.instance.routes

    @routes.post("/orion4d_auto_cached_preview/paste")
    async def orion4d_auto_cached_preview_paste(request):
        content_length = request.content_length
        if content_length is not None and content_length > MAX_CLIPBOARD_IMAGE_BYTES:
            return _json_error("Clipboard image is too large.", status=413)

        try:
            post = await request.post()
            node_id = post.get("node_id", "node")
            image_field = post.get("image")

            if image_field is None or not hasattr(image_field, "file"):
                return _json_error("No image file received from clipboard.")

            pil_img = Image.open(image_field.file)
            result = _NODE_HELPER._paste_pil_to_cache(pil_img, node_id)
            _send_executed_ui_update(node_id, result["ui"])

            return web.json_response({
                "ok": True,
                "ui": result["ui"],
                "meta": result["meta"],
            })
        except Exception as exc:
            return _json_error(f"Could not paste clipboard image: {exc}", status=500)

    @routes.post("/orion4d_auto_cached_preview/clear")
    async def orion4d_auto_cached_preview_clear(request):
        try:
            post = await request.post()
            node_id = post.get("node_id", "node")
            removed = _NODE_HELPER._clear_node_cache(node_id)
            ui = {
                "images": [],
                "text": [f"AutoCachedPreview [{_safe_id(node_id)}] cache cleared"],
            }
            _send_executed_ui_update(node_id, ui)
            return web.json_response({
                "ok": True,
                "removed": removed,
                "ui": ui,
                "meta": {"revision": time.time_ns()},
            })
        except Exception as exc:
            return _json_error(f"Could not clear cache: {exc}", status=500)


NODE_CLASS_MAPPINGS = {
    "AutoCachedPreview": AutoCachedPreview,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AutoCachedPreview": "Auto Cached Preview (Image + Mask)",
}
