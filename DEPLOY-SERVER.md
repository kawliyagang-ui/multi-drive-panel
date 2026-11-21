# Server-e Panel Bosano — Deploy Guide

Panel ta server-e bosale phone theke jekono jayga theke upload / download /
play / delete kora jabe, PC chalu rakha lagbe na.

## ⚠️ Agei jana kotha

- **InfinityFree-te eta cholbe na** — okhane sudhu PHP/WordPress chole, Python
  server chole na. Tai Cinetra ekhon jekhane ache sekhane panel bosano jabe na.
- Panel er **index database (data folder)** server-e *persistent* (sthayi)
  jaygay thakte hobe. Restart-e data muche jay emon free hosting-e asol
  Gmail connect kore lav nai — token + index hariye jabe.

## Server er option gula (sotti hishab)

| Option | Prothom payment | Obostha |
|---|---|---|
| **Render Free** | **$0** | Sudhu test er jonno. 15 min na use korle ghumiye jay, restart-e data reset — asol account er jonno na |
| **Render Starter + 1 GB disk** | **~$7.25/month theke** (service ~$7 + disk ~$0.25), monthly | Sohoj, kintu tomar $1–2 budget er baire |
| **Sosta VPS (Hetzner / Contabo type)** | **Motamuti $4–6/month**, monthly billing | Sobcheye valo long-term. Root access thake, Docker diye ekbare boshe. Tobe eta o $1–2 er upore |
| **Oracle Cloud Free** | **$0** | Age ekbar card verification-e atke chhile (ZarPay card) — onno card thakle abar try kora jay, free te VPS pawa jay |

**Poramorsho:** age **Render Free**-te bosiye phone diye chaliye dekho panel
tomar kajer kina (taka khoroch $0). Valo lagle tokhon VPS er siddhanto nio —
$1–2 te sthayi VPS paoya jay na bollei chole, tai ei khoroch ta alada kore
vabte hobe.

---

## Upay A — Render (sohoj, browser thekei)

1. Code ta ekta **GitHub** repository-te rakho (private)
2. **render.com**-e signup → New → Web Service → repo select koro
   (repo-te `render.yaml` ache, Render nijei settings niye nibe)
3. Environment-e boshao: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
   `BASE_URL` = Render je URL ta dibe (jemon `https://multi-drive-panel.onrender.com`)
4. Google Cloud Console-e redirect URI-te boshao:
   `https://tomar-url.onrender.com/auth/callback`
5. Deploy → URL khule panel paba

## Upay B — VPS (Docker, sthayi)

Server-e SSH diye dhuke:

```bash
# Docker na thakle age install koro (Ubuntu):
curl -fsSL https://get.docker.com | sh

# Code ano (ZIP upload ba git clone), tarpor folder-e giye:
cd multi-drive-panel
cp .env.example .env
nano .env        # CLIENT_ID, SECRET, BASE_URL=https://tomar-domain boshao

docker build -t drive-panel .
docker run -d --name drive-panel --restart unless-stopped \
  -p 8000:8000 -v panel-data:/app/data --env-file .env drive-panel
```

- `panel-data` volume-e index DB + token key thakbe — container restart
  holeo kichu harabe na
- Domain chaile (jemon `panel.cinetra.site`) DNS-e server er IP bosiye
  Caddy/Nginx reverse proxy lagbe — seta bosanor somoy kore deya jabe
- Google Cloud Console-e redirect URI: `https://tomar-domain/auth/callback`

## Mone rakho

- `.env` er SECRET karo shathe share na
- Server-e panel kholar address tai holo tomar panel — phone er browser-e
  bookmark kore rekho
- Majhe majhe `data` folder (ba volume) er backup nio — index hariye gele
  Drive-e file thakleo khuje pawa jabe na
