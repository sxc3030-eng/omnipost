# GeniA Pipeline (v1.1)

Cross-posting automatisé depuis **GIa Underground** (https://genia.social) vers Facebook, Instagram, TikTok, etc.

## Workflow

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  FABRICATION │ ──► │  CONVERSION  │ ──► │ APPROBATION  │ ──► │ PUBLICATION  │
│   (ingest)   │     │   (ffmpeg)   │     │   (manuel)   │     │   (drip)     │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
   pipeline/         pipeline/             pipeline/             pipeline/
   created/          converted/            approved/             published/
   (auto, 5min)      (auto, 2min)          (WS commande)         (1/jour, 14h)
```

### 1. Fabrication (auto)
Polls `https://api.genia.social/rest/posts` toutes les 5 min. Pour chaque nouveau post :
- Télécharge `cover.jpg`, `audio.mp3` (preview Spotify), `source.mp4` (si vidéo)
- Génère les captions par plateforme avec lien `https://genia.social/post/<id>`
- Dépose dans `pipeline/created/<post_id>/`

### 2. Conversion (auto)
Toutes les 2 min, scanne `pipeline/created/` :
- Si vidéo source → letterbox 9:16 (1080×1920)
- Si image seule → Ken Burns zoom-pan + texte artiste/album + audio Spotify
- Sortie `pipeline/converted/<id>/tiktok.mp4`

### 3. Approbation (manuel)
Via WebSocket dashboard :
```js
ws.send({ cmd: "pipeline_status" })           // counts par phase
ws.send({ cmd: "pipeline_list", phase: "converted" })
ws.send({ cmd: "pipeline_approve", ids: "all" })   // ou liste d'IDs
ws.send({ cmd: "pipeline_reject", ids: ["..."] })
```

### 4. Publication drip (auto)
Tous les 10 min après l'heure cible (`drip_hour`, défaut 14h) :
- Pioche le plus ancien `approved`
- Publie via OmniPost (auto_publish=true) ou stage en scheduled
- Compteur `published_today` reset à minuit
- Défaut : **1 post/jour** — ajustable via `drip_per_day`

## Configuration (`omnipost_settings.json`)

```json
{
  "genia": {
    "enabled": true,
    "api_url": "https://api.genia.social",
    "api_token": "",
    "platforms": ["instagram", "facebook", "tiktok"],
    "default_hashtags": ["#metal", "#underground", "#GIaUnderground"],
    "credit_text": "via GIa Underground 🤘 https://genia.social",
    "poll_seconds": 300,
    "lookback_hours": 24,
    "convert_seconds": 120,
    "video_duration": 30,
    "drip_per_day": 1,
    "drip_hour": 14,
    "drip_seconds": 600,
    "auto_publish": true
  }
}
```

## Pré-requis

- Python 3.10+
- `ffmpeg` installé (`apt install ffmpeg fonts-dejavu-core` sur Debian)
- Connexions OAuth configurées dans OmniPost pour les plateformes cibles

## Lancement

OmniPost démarre automatiquement les 3 boucles si `genia_pipeline.py` est présent :
```
python omnipost.py
```

Logs visibles :
```
[GENIA] Listener simple actif
[PIPELINE] Fabrication → Conversion → Approbation → Drip actif
[pipeline] root=/srv/omnipost/pipeline
[ingest] 3 new posts to fabricate
[convert] abc-123 -> converted/
```

## Compatibilité

Le pipeline **n'écrase rien** :
- L'ancien posting manuel via dashboard fonctionne toujours
- Le `genia_listener.py` simple reste actif (peut être désactivé via `genia.enabled=false`)
- Tout le reste d'OmniPost (scheduler, OAuth, analytics) inchangé
