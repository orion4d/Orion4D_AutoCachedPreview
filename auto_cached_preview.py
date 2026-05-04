import json
import os
import re
from typing import Optional, Tuple

import numpy as np
from PIL import Image, PngImagePlugin
import torch

import folder_paths


CACHE_SUBFOLDER = "AutoCachedPreview"


def _safe_id(value) -> str:
    value = str(value) if value is not None else "node"
    value = re.sub(r"[^a-zA-Z0-9_\-]", "_", value)
    return value or "node"


def _cache_root() -> str:
    root = os.path.join(folder_paths.get_temp_directory(), CACHE_SUBFOLDER)
    os.makedirs(root, exist_ok=True)
    return root


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

    def _node_paths(self, unique_id: str) -> Tuple[str, str, str]:
        root = _cache_root()
        safe_uid = _safe_id(unique_id)
        tensor_path = os.path.join(root, f"{safe_uid}_cache.pt")
        meta_path = os.path.join(root, f"{safe_uid}_meta.json")
        image_prefix = f"{safe_uid}_preview"
        return tensor_path, meta_path, image_prefix

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

        for idx in range(image.shape[0]):
            pil_img = _tensor_to_pil(image[idx])
            filename = f"{image_prefix}_{idx:03d}.png"
            path = os.path.join(root, filename)

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
            preview_items.append({"filename": filename, "subfolder": CACHE_SUBFOLDER, "type": "temp"})

        return preview_items

    def _save_cache(self, image: torch.Tensor, mask: torch.Tensor, unique_id: str):
        tensor_path, meta_path, _ = self._node_paths(unique_id)

        payload = {
            "image": image.detach().cpu(),
            "mask": mask.detach().cpu(),
        }
        torch.save(payload, tensor_path)

        meta = {
            "unique_id": str(unique_id),
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

    def run(self, fallback_to_cache=True, image=None, mask=None, unique_id=None, prompt=None, extra_pnginfo=None):
        used_cache = False

        if image is not None:
            out_image = image
            out_mask = self._normalize_mask(out_image, mask)
            self._save_cache(out_image, out_mask, unique_id)
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


NODE_CLASS_MAPPINGS = {
    "AutoCachedPreview": AutoCachedPreview,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AutoCachedPreview": "Auto Cached Preview (Image + Mask)",
}
