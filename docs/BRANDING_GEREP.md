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
   `</head>` par l'injection de deux balises **avant** la fermeture du `<head>` :
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
| [`../nginx/branding/gerep-logo.svg`](../nginx/branding/gerep-logo.svg) | Logo officiel (substitué dans l'UI par CSS, cf. §4) |
| [`../nginx/onyx.conf`](../nginx/onyx.conf) | Câblage **dev** (sub_filter + `location /onix-branding/`) |
| [`../deploy/prod/nginx.prod.conf`](../deploy/prod/nginx.prod.conf) | Câblage **prod** (même surcouche, derrière Caddy) |
| [`../docker-compose.yml`](../docker-compose.yml) · [`../deploy/prod/docker-compose.prod.yml`](../deploy/prod/docker-compose.prod.yml) | Montage du volume `./nginx/branding` dans nginx (dev + prod) |

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

## 4. Nom d'application et logo — **FOSS vs EE** (point d'honnêteté)

> ⚠️ **Le whitelabel ADMIN d'Onyx (nom d'app + upload de logo dans
> *Admin → Settings*) est une fonctionnalité Enterprise Edition (EE), PAS
> FOSS.** Preuve (audit byte-level) :
> `ee/onyx/server/enterprise_settings/models.py:application_name, use_custom_logo`
> — colonne **CE = « No »** dans
> [`docs/audit-onyx/70-oss-health-licensing.md`](audit-onyx/70-oss-health-licensing.md)
> (§ *Enterprise Settings & Whitelabeling*). L'écriture
> `/admin/enterprise-settings` exige le tier **Business** ; masquer
> « Powered by Onyx » est aussi EE.

onix tourne sur les images **Community Edition** (`onyxdotapp/onyx-backend`,
`onyxdotapp/onyx-web-server` — cf. `docker-compose.yml`, sans
`ENABLE_PAID_ENTERPRISE_EDITION_FEATURES`). **Ce panneau admin n'existe donc
pas** dans notre déploiement. On obtient le **même résultat en FOSS** via la
surcouche nginx que l'on maîtrise — aucun coût de licence :

- **Logo** : `gerep-theme.css` (§ *4) LOGO GEREP*) substitue le logo Onyx par
  [`gerep-logo.svg`](../nginx/branding/gerep-logo.svg) via CSS (`content:` sur
  les `<img>` de logo + lien de marque vers l'accueil). Un bloc **prêt à
  activer** couvre le cas d'un logo en **SVG inline** (masquage + fond).
- **Nom d'app (onglet navigateur)** : `nginx.*conf` réécrit
  `<title>Onyx</title>` → `GEREP — Assistant Client 360` via `sub_filter`.

> **Caveat best-effort** (cf. §6) : substitution de logo et réécriture de titre
> dépendent du markup réel d'Onyx. Le titre peut être **re-fixé côté client**
> par Next.js après hydratation (titre de conversation) ; les sélecteurs de logo
> sont à **confirmer en live** via l'inspecteur. Sans EE, c'est la meilleure
> approche ; avec un abonnement Onyx Business+, le whitelabel admin reste l'option
> « officielle » (et permet de retirer la mention « Powered by Onyx »).

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

## 7. Production — surcouche reportée ✅

La même surcouche est désormais appliquée au chemin de **production** :

- **`deploy/prod/nginx.prod.conf`** : `location /onix-branding/` + injection
  `sub_filter` (thème, favicon, titre) dans `location /`, à l'identique du dev.
- **`deploy/prod/docker-compose.prod.yml`** : la conf prod fait `!reset []` sur
  les volumes nginx ; le volume `./nginx/branding:/etc/nginx/branding:ro` est
  donc **re-monté explicitement** après le reset.
- **`deploy/prod/Caddyfile`** : **aucun changement requis**. Caddy relaie tout
  vers le nginx interne (y compris `/onix-branding/*` via le `handle` catch-all) ;
  l'injection a lieu au niveau de nginx. Caddy peut recompresser la réponse
  finale (sans effet, l'injection est déjà faite). Le CSS est servi en **même
  origine** → compatible avec une CSP stricte (`style-src 'self'`).

### Reste (hors périmètre docker-compose)

- **Kubernetes** (`deploy/k8s/onix-ha`) : le web est servi directement par le
  pod `web_server` (pas de hop nginx avec `sub_filter`). Y reporter le branding
  demanderait un sidecar nginx ou une `ConfigMap` montée — **lot dédié** si un
  déploiement K8s public est visé.
