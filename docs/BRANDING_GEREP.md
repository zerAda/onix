# Branding GEREP de l'UI Onyx

> Comment l'interface web Onyx (chat) est habillée aux couleurs **GEREP** —
> « votre conseiller en protection sociale » — via une **surcouche versionnée**,
> sans forker le frontend.

## 1. Principe : surcouche, pas de fork

Le frontend Onyx est livré comme **image préfabriquée** (`onyx-web-server`,
Next.js). On ne le forke **pas**. À la place, nginx — déjà unique point d'entrée
de la stack — **injecte** notre feuille de style et notre favicon dans le HTML
servi, à la volée.

Mécanisme (`ngx_http_sub_module`, inclus dans l'image nginx officielle) :

1. `location /` désactive la compression amont (`proxy_set_header Accept-Encoding "";`)
   pour que le filtre puisse lire le HTML.
2. `sub_filter` (restreint à `text/html`, `sub_filter_once off`) remplace
   `</head>` par l'injection de deux balises **avant** la fermeture du `<head>** :
   ```html
   <link rel="stylesheet" href="/onix-branding/gerep-theme.css">
   <link rel="icon" type="image/svg+xml" href="/onix-branding/favicon.svg">
   ```
3. `location /onix-branding/` sert les fichiers statiques (CSS, logo, favicon)
   depuis le volume `./nginx/branding` monté en lecture seule.

Comme le CSS est chargé **en dernier**, il gagne en cascade : il surcharge les
**design-tokens** OPAL/Onyx (`@onyx-ai/opal` + Tailwind) et applique des
**fallbacks** ciblés. L'API, le WebSocket/streaming et les ressources non-HTML
(JS, JSON, CSS, binaires) **ne sont pas touchés**.

### Fichiers de la surcouche

| Fichier | Rôle |
|---|---|
| [`../nginx/branding/gerep-theme.css`](../nginx/branding/gerep-theme.css) | Thème GEREP (tokens + surcharge OPAL + fallbacks) |
| [`../nginx/branding/favicon.svg`](../nginx/branding/favicon.svg) | Favicon dérivé du « mark » du logo |
| [`../nginx/branding/gerep-logo.svg`](../nginx/branding/gerep-logo.svg) | Logo officiel (pour l'upload admin, cf. §4) |
| [`../nginx/onyx.conf`](../nginx/onyx.conf) | Câblage (sub_filter + `location /onix-branding/`) |
| [`../docker-compose.yml`](../docker-compose.yml) | Montage du volume `./nginx/branding` dans nginx |

## 2. Palette officielle et rôles

Couleurs extraites du logo GEREP :

| Rôle | Token | Hex | Usage |
|---|---|---|---|
| **Primaire** | `--gerep-primary` | `#094785` | Bleu profond : barres, boutons primaires, focus, liens, éléments actifs |
| **Accent** | `--gerep-accent` | `#E96D11` | Orange : CTA et mises en avant **parcimonieuses** |
| **Secondaire** | `--gerep-secondary` | `#B6B8DD` | Lavande : surfaces douces, états subtils, sélection de texte |

Chaque couleur a des **nuances dérivées** (`-hover`, `-active/-strong`,
`-subtle`, `-tint`) et des états (`--gerep-focus-ring`), tous définis en tête du
CSS comme **source de vérité unique**.

## 3. Accessibilité (WCAG AA)

Ratio de contraste texte visé **≥ 4.5:1** :

- Texte **blanc sur `#094785`** ≈ **8.6:1** ✓ → boutons primaires, titres sur bleu.
- Texte **`#094785` sur blanc** ≈ **8.6:1** ✓ → liens, libellés.
- L'**orange `#E96D11`** est trop clair pour du **petit texte blanc** (~3.0:1).
  Règle GEREP appliquée : orange en **aplat de CTA avec texte foncé**
  (`--gerep-accent-contrast: #1f1300`), ou comme liseré/accent décoratif —
  **jamais** en petit texte orange sur fond clair. Au survol, l'orange foncit
  (`--gerep-accent-strong: #c25709`) et repasse AA avec du texte blanc (~4.6:1).
- **Focus clavier** toujours visible : anneau bleu GEREP (`:focus-visible`) +
  halo sur les champs de saisie.
- Les **gris/neutres** OPAL sont conservés pour la lisibilité du contenu
  (réponses, documents) : on ne re-teinte que les accents de marque.

## 4. Whitelabel admin Onyx (à faire en complément)

La surcouche CSS gère les **couleurs**. Le **nom d'application** et le **logo**
affichés dans l'UI se règlent via l'**admin Onyx** (whitelabel FOSS) :

1. Se connecter avec un compte **admin**.
2. Aller dans **Admin Panel → Settings → Whitelabeling** (ou
   *Workspace Settings* selon la version).
3. **Application Name** : saisir `GEREP`.
4. **Logo** : téléverser [`nginx/branding/gerep-logo.svg`](../nginx/branding/gerep-logo.svg)
   (et un logo « collapsed »/icône si le champ existe — réutiliser le favicon).
5. Enregistrer, puis recharger l'UI (Ctrl+F5).

> Ces réglages sont stockés en base (persistés) : à refaire une seule fois par
> environnement.

## 5. Ajuster le thème

Tout est piloté par les variables `--gerep-*` **en tête** de
`gerep-theme.css`. Pour changer une teinte, modifier **une** variable et
recharger l'UI (Ctrl+F5) — le volume est monté en RO, nginx ressert le fichier
modifié immédiatement (pas de rebuild d'image).

Le mode **sombre** (`.dark`, appliqué par Onyx) est géré : le bleu GEREP est
**éclairci** pour rester vif et contrasté sur fond foncé, et les liens
passent à une teinte plus claire et lisible.

## 6. Caveat honnête (à lire avant le réglage fin)

> La surcouche est **best-effort** sur un frontend **préfabriqué** dont les noms
> de classes/jetons peuvent varier selon la version Onyx. La surcharge des
> **tokens OPAL** couvre l'essentiel ; les **fallbacks** par sélecteurs larges
> sont un filet de sécurité **volontairement générique**.
>
> Un **ajustement fin des sélecteurs est à faire en live** (sur le poste), une
> fois l'UI réellement visible : ouvrir l'inspecteur, repérer les classes/jetons
> effectivement utilisés, et affiner les règles. Le CSS est commenté et entièrement
> pilotable depuis les variables pour rendre ce réglage rapide.

## 7. TODO — suivi production

Le déploiement de prod utilise une autre conf nginx :
**`deploy/prod/nginx.prod.conf`**. La même surcouche (sub_filter +
`location /onix-branding/` + montage du volume branding) **reste à reporter**
sur le chemin prod (et sur la conf Caddy/`deploy/prod/Caddyfile` si le branding
y transite). À traiter dans un lot dédié au scope `deploy/prod`.
