# Plan Capital — console perso

Page statique + 2 automatisations GitHub Actions au-dessus des bases Notion « Plan Capital ».
Notion reste la source de vérité ; ici on lit, on calcule, on affiche, et on rappelle.

```
Notion (3 bases) ──► sync.yml (jours de bourse, 08:30) ──► docs/data.json ──► GitHub Pages
                                                       └─► (option) écrit la valeur PEA dans Notion
                     reminder.yml (le 6 du mois) ───────► ntfy sur le téléphone ou issue GitHub
```

## Mise en place (15 minutes, une seule fois)

### 1. Intégration Notion
1. https://www.notion.so/profile/integrations → **Nouvelle intégration** (interne), nom `capital-console`.
2. Capacités : lecture + mise à jour du contenu. Copier le **secret** (commence par `ntn_`).
3. Dans Notion, ouvrir la page **Plan Capital — 20 → 30 ans** → `…` en haut à droite → **Connexions** → ajouter `capital-console`.
   Les trois bases héritent de l'accès.

### 2. Dépôt GitHub (tout se passe ici, gratuit)
1. Créer un dépôt **public** `capital-console` (Pages gratuit = dépôt public ; les données sont chiffrées, voir ci-dessous), y pousser ce dossier.
2. **Settings → Secrets and variables → Actions → Secrets** :
   - `NOTION_TOKEN` = le secret de l'étape 1.
   - `DATA_PASSPHRASE` = une phrase de passe longue (20+ caractères, gestionnaire de mots de passe). C'est elle que tu taperas sur la page. Sans elle, le dépôt contient un `data.enc` illisible.
3. **Variables** (même écran, onglet Variables) :
   - `NTFY_TOPIC` = un nom de topic unique et impossible à deviner, ex. `kevin-pea-7f3a9c` (sinon le rappel ouvre une issue GitHub).
   - `NOTION_WRITE_BACK` = `true` si tu veux que le bot mette à jour « PEA valeur titres » du dernier mois dans Notion. Laisse vide au début.
4. **Settings → Pages** : Source = *Deploy from a branch*, branche `main`, dossier `/docs`.
5. **Actions** → *Sync Notion → data.json* → **Run workflow**. Une minute plus tard, `docs/data.enc` est committé et la page est en ligne sur `https://<ton-user>.github.io/capital-console/`. Elle demande la phrase de passe, déchiffre dans le navigateur (Web Crypto, AES-256-GCM), et peut la mémoriser sur ton appareil.

Dépôt privé + GitHub Pro (~4 $/mois) : même procédure, tu peux omettre `DATA_PASSPHRASE` et la page lira `data.json` en clair.
Homelab : `git clone` + nginx qui sert `docs/` + cron `git pull`, derrière ton VPN.

### 3. Téléphone (optionnel)
Installer l'app **ntfy** (Android / iOS), s'abonner au topic `NTFY_TOPIC`. Le 6 du mois, si aucun achat n'est enregistré dans **Ordres PEA**, tu reçois une notif.

## Ce que fait `scripts/sync.py`
- Lit **Ordres PEA** → parts détenues par support, montant investi (les lignes « MODÈLE » sont ignorées).
- Récupère le dernier cours via Yahoo Finance (`WPEA.PA`, `CW8.PA`, `ESE.PA`).
- Calcule valeur du PEA, plus-value latente, capital total « du jour ».
- Lit **Suivi mensuel** et **Watchlist**, écrit tout dans `docs/data.enc` (chiffré) ou `docs/data.json` (clair si pas de `DATA_PASSPHRASE`).
- `--check-order` : code de sortie 1 s'il n'y a pas d'achat ce mois-ci (utilisé par le rappel).

Tester en local :
```bash
pip install -r scripts/requirements.txt
NOTION_TOKEN=ntn_xxx python scripts/sync.py                        # → docs/data.json en clair
NOTION_TOKEN=ntn_xxx DATA_PASSPHRASE='ma phrase' python scripts/sync.py   # → docs/data.enc
python -m http.server -d docs 8000   # puis http://localhost:8000
```

## Adapter
- Nouveau support dans Notion → ajouter son ticker Yahoo dans `TICKERS` (sync.py).
- Changer l'heure du sync → `cron` dans `.github/workflows/sync.yml` (UTC).
- Les scénarios de projection sont dans `PROJECTION` (sync.py), mêmes chiffres que la page Notion.

## Limites assumées
- Avec `DATA_PASSPHRASE`, ce qui est public c'est : le code, les tickers, et un blob chiffré. Pas tes soldes. Sans la phrase de passe, rien n'est lisible — et si tu la perds, le prochain sync la régénère, tu ne perds rien (Notion reste la source).
- Les secrets Actions (`NOTION_TOKEN`, `DATA_PASSPHRASE`) ne sont jamais écrits dans le dépôt ni visibles dans les logs.
- Yahoo Finance peut être en retard d'un jour ou indisponible : le script continue sans cours, la page affiche « — ».
- Pas de mot de passe, pas de base de données, pas de serveur. C'est voulu.
