🇬🇧 English version (default)  
🇫🇷 [Lire la version française](README_FR.md)

# Auto Cached Preview

<img width="1892" height="865" alt="image" src="https://github.com/user-attachments/assets/1fbcfad3-0d67-4f5f-b337-161f28284d8d" />

A custom node for ComfyUI that allows you to cache an image and its mask to avoid recalculating upstream steps in your workflow.

Ideal for single-image creation workflows where you want to adjust final parameters (upscale, filters, color correction, inpainting) without having to rerun your KSamplers.
---

## ✨ Features

* **Automatic caching:** Saves the last received image and its mask to the disk.
* **Smart fallback:** If you disconnect the node's `image` input, it instantly reloads the last cached image.
* **Automatic mask management:** Automatically extracts the mask from the image's Alpha channel if it exists. Otherwise, generates a default empty mask.
* **Startup cleanup:** The temporary cache folder is automatically cleared every time ComfyUI is launched to prevent filling up your hard drive.
* **Metadata preservation (PNG Info):** Generated preview images retain the prompt and the complete workflow. You can still drag and drop the preview into ComfyUI to reload your workspace.

---

## 🛠️ Usage

The node is located in the category: `orion4D_image/preview` under the name **Auto Cached Preview (Image + Mask)**.

1. **Initial generation:** Connect the output of your KSampler (or VAE Decode) to the `image` input of the node. Run the generation. The image is displayed and cached.
2. **Using the cache:** Disconnect the incoming link to the `image` input. Ensure the `fallback_to_cache` option is set to `True`.
3. **Real-time adjustments:** Restart your workflow (`Queue Prompt`). The node will instantly load the image from the disk. You can continue working on downstream nodes without recalculating the original image.

### Inputs
* **image** (optional): The incoming image tensor to be cached.
* **mask** (optional): The mask tensor. If not provided, the node deduces it from the image or creates a default one.
* **fallback_to_cache** (BOOLEAN): If enabled (`True`), the node will use the cached image if the image input is disconnected.

### Outputs
* **image**: The image (either the input one or the one retrieved from the cache).
* **mask**: The associated mask.

---

## 📁 Technical details

Cache files (`.pt`) and previews (`.png`) are saved in the ComfyUI temporary folder, under the `AutoCachedPreview` subfolder. This folder is purged at every startup via the `__init__.py` script to ensure healthy disk space management.

---

<div align="center">

Made with ❤️ for the ComfyUI community · **Orion4D**

</div>
