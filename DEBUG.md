# Où déboguer — carte du système

Ce document répond à une seule question : **quand ça casse, où je regarde ?**

Il n'y a aucune intelligence artificielle dans la boucle de publication. Ce
sont des boucles Python et des appels HTTP. Tout est inspectable.

---

## 1. Qui fait quoi

```
La Guilde  (autre dossier, autre programme)
  │  fabrique un paquet : PNG + MP4 + JSON + TXT
  │  deposer_post()  →  écrit dans le dossier ci-dessous
  ▼
pipeline/converted/            ←── la frontière entre les deux programmes
  │
  ▼
omnipost.py  (le serveur, une seule fenêtre PowerShell)
  ├── serveur WebSocket   ws://localhost:8860   ← parle au tableau de bord
  ├── serveur OAuth      http://localhost:8861   ← reçoit les retours Facebook
  └── quatre boucles de fond :
        _ingest_loop    genia_pipeline.py:286   récupère depuis genia.social
        _convert_loop   genia_pipeline.py:469   ffmpeg → vidéo 9:16
        _drip_loop      genia_pipeline.py:646   publie N par jour à l'heure dite
        scheduler_loop  omnipost.py:1058        posts programmés à la main
  ▼
_publish_to_platform()  omnipost.py
  └── _post_facebook / _post_instagram / _post_tiktok
      _post_youtube / _post_pinterest / _post_twitter
```

Le tableau de bord (`omnipost_dashboard.html`) n'est qu'une **façade**. Il ne
publie rien lui-même : il envoie des commandes au serveur par le WebSocket.
Si le voyant n'est pas vert, rien de ce que tu cliques n'arrive.

---

## 2. Où vit l'état

| Fichier | Contient | Regarder quand |
|---|---|---|
| `omnipost_settings.json` | clés OAuth, **jetons**, réglages `genia` | connexion perdue, publication refusée |
| `omnipost_posts.json` | tous les posts et **le motif exact des erreurs** | une publication a échoué |
| `omnipost.log` | journal d'exécution | toujours, en premier |
| `pipeline/` | `created` → `converted` → `approved` → `published` / `failed` | un post ne part pas |
| `pipeline_drip_state.json` | combien publiés aujourd'hui | le drip ne publie plus |
| `pipeline_ingest_state.json` | ids déjà importés | un post n'est jamais repris |
| `media/` | fichiers téléversés depuis le tableau de bord | le média n'arrive pas |

**Le plus utile est `omnipost_posts.json`.** Le journal dit ce qui a échoué ;
ce fichier dit *pourquoi*.

```powershell
python -c "import json;p=[x for x in json.load(open('omnipost_posts.json',encoding='utf-8')) if x.get('results')][0];[print(k,'->',v.get('status'),'|',v.get('error') or v.get('url') or '') for k,v in p['results'].items()]"
```

Note le `[0]` : les posts sont insérés **en tête** de liste, le plus récent est
donc le premier.

---

## 3. Diagnostic par symptôme

### Le tableau de bord est vide, voyant « Reconnexion… »
Le serveur ne tourne pas. `Get-Process python`. Relancer avec le `.bat`.

### Je clique et rien ne se passe
Le navigateur a gardé l'ancien HTML. **Ctrl+F5**, ou une fenêtre privée
(Ctrl+Shift+N) qui n'a pas de cache.

### « Media was not uploaded to the backend (blob: URL) »
Même cause : vieux HTML en cache. Le fichier n'a jamais quitté le navigateur.
La preuve que c'est réglé, c'est cette ligne au moment de joindre le fichier :

```
[MEDIA] media\xxxx.mp4 (2163078 bytes)
```

### « Error validating client secret »
La clé secrète du fichier de réglages ne correspond pas à l'App ID.
Vérifier qu'elles viennent bien de la même app Meta.

### « This authorization code has been used »
Un code OAuth ne sert qu'une fois. Ne jamais recharger l'onglet de retour :
repartir du bouton **Connecter**.

### « connecté » mais toutes les publications échouent
Vérifier que le jeton existe réellement :

```powershell
$j = Get-Content omnipost_settings.json -Raw | ConvertFrom-Json
$j.accounts.facebook.page_name
if ($j.accounts.facebook.access_token) { "jeton PRESENT" } else { "jeton ABSENT" }
```

`ABSENT` signifie que l'échange de jeton n'a pas eu lieu — reconnecter.

### Instagram refuse tout
Normal si on lui passe un fichier local. Son API ne téléverse rien : elle va
chercher le média elle-même et exige une **URL publique**. Il faut héberger
d'abord.

### Le drip ne publie plus
Dans l'ordre : `enabled` est-il à `true` ? l'heure `drip_hour` est-elle passée ?
`drip_days` contient-il le jour d'aujourd'hui (lundi = 0) ? le quota
`drip_per_day` est-il déjà atteint dans `pipeline_drip_state.json` ? y a-t-il
seulement quelque chose dans `pipeline/approved/` ?

### Rien ne sort de La Guilde
L'agent `filtre-metal` est-il semé ? Sans lui, `filtre_metal: true` bloque tout
— c'est délibéré : mieux vaut ne rien publier que publier hors ligne
éditoriale. `python -m guilde agents` pour vérifier.

### git refuse de tirer, « Unlink of file omnipost.log failed »
Le serveur tourne et garde le journal ouvert. L'arrêter d'abord.

---

## 4. Les trois vérifications qui règlent la majorité des cas

```powershell
cd D:\omnipost

# 1. le serveur tourne-t-il ?
Get-Process python -ErrorAction SilentlyContinue

# 2. qu'a dit le journal ?
Get-Content omnipost.log -Tail 30

# 3. pourquoi le dernier post a-t-il échoué ?
python -c "import json;p=[x for x in json.load(open('omnipost_posts.json',encoding='utf-8')) if x.get('results')][0];[print(k,'->',v.get('status'),'|',v.get('error') or '') for k,v in p['results'].items()]"
```

---

## 5. Ce qui est volontairement bloquant

Deux garde-fous refusent de publier plutôt que de publier mal. Si tout
s'arrête sans erreur visible, ce sont eux :

- **`filtre_metal`** — un genre non identifié n'est pas publié.
- **`gardien-publication`** — droits non précisés, expéditeur inconnu ou
  embargo envoient le post en approbation humaine.

Ils se désactivent dans les réglages, mais c'est un choix éditorial, pas une
panne.
