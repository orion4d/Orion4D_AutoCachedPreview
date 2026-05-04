# Auto Cached Preview

Un nœud personnalisé  pour ComfyUI qui permet de mettre en cache une image et son masque pour éviter de recalculer les étapes en amont de votre workflow.

Idéal pour les workflows de création d'image unique où vous souhaitez ajuster des paramètres finaux (upscale, filtres, correction colorimétrique, inpainting) sans avoir à relancer vos KSamplers.
---

## ✨ Fonctionnalités

* **Mise en cache automatique :** Enregistre la dernière image reçue et son masque sur le disque.
* **Fallback intelligent :** Si vous déconnectez l'entrée `image` du nœud, il recharge instantanément la dernière image mise en cache.
* **Gestion automatique des masques :** Extrait automatiquement le masque du canal Alpha de l'image si celui-ci existe. Sinon, génère un masque vide par défaut.
* **Nettoyage au démarrage :** Le dossier de cache temporaire est automatiquement vidé à chaque lancement de ComfyUI pour éviter de saturer votre disque dur.
* **Conservation des métadonnées (PNG Info) :** Les images de prévisualisation générées conservent le prompt et le workflow complet. Vous pouvez toujours glisser-déposer la preview dans ComfyUI pour recharger votre espace de travail.

---

## 🛠️ Utilisation

Le nœud se trouve dans la catégorie : `orion4D_image/preview` sous le nom **Auto Cached Preview (Image + Mask)**.

1. **Génération initiale :** Connectez la sortie de votre KSampler (ou VAE Decode) à l'entrée `image` du nœud. Lancez la génération. L'image s'affiche et est mise en cache.
2. **Utilisation du cache :** Déconnectez le lien entrant vers l'entrée `image`. Assurez-vous que l'option `fallback_to_cache` est sur `True`.
3. **Ajustements en temps réel :** Relancez votre workflow (`Queue Prompt`). Le nœud chargera instantanément l'image depuis le disque. Vous pouvez continuer à travailler sur les nœuds en aval sans recalculer l'image d'origine.

### Entrées
* **image** (optionnel) : Le tenseur de l'image entrante à mettre en cache.
* **mask** (optionnel) : Le tenseur du masque. Si non fourni, le nœud le déduit de l'image ou en crée un par défaut.
* **fallback_to_cache** (BOOLEAN) : Si activé (`True`), le nœud utilisera l'image en cache si l'entrée image est déconnectée.

### Sorties
* **image** : L'image (soit celle en entrée, soit celle récupérée du cache).
* **mask** : Le masque associé.

---

## 📁 Fonctionnement technique

Les fichiers de cache (`.pt`) et les prévisualisations (`.png`) sont sauvegardés dans le dossier temporaire de ComfyUI, sous le sous-dossier `AutoCachedPreview`. Ce dossier est purgé à chaque démarrage grâce au script `__init__.py` pour garantir une gestion saine de l'espace disque.

---

<div align="center">

Made with ❤️ for the ComfyUI community · **Orion4D**

</div>
