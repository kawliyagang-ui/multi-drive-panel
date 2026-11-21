# Multi-Drive Storage Pool — Setup Guide
Panel v1 (Prototype) — Anik er jonno

---

## 1. Panel ta ki

- Web panel: phone/PC er browser thekei cholbe, kichu install korte hobe na (server-e cholbe)
- Minimum **5 ta Gmail** account connect lagbe, maximum **kono limit nai**
- Boro file (1 GB, 2 GB+) **100 MB / 200 MB tukra** hoye **alada alada Gmail account-e** thakbe
- Download / streaming er somoy sob tukra **ekshathe jora lege ekta file** hisebe asbe
- Test kore dekha hoiye geche: 210 MB file → 3 ta tukra (100+100+10 MB) 3 ta alada account-e geche, download-e original file er hash (porichoy) 100% mile geche, majhkhan theke streaming (range) o kaj koreche ✅

---

## 2. Age Test Mode-e try koro (Google setup charai)

1. Server chalao (Section 4)
2. Browser-e panel kholo → **"Test Mode: 5 ta Demo Account"** button chapo
3. Ekhon file upload, download, play, delete — sob try korte parba
   (Demo account gula asole ei computer er folder — asol Drive na, sudhu test er jonno)

---

## 3. Asol Gmail connect er setup (ekbar, ~10 minute)

### Step A — Google Cloud project
1. **console.cloud.google.com**-e jao (jekono ekta Gmail diye login)
2. Upar theke **New Project** → nam dao jemon `multi-drive-pool` → Create
3. Left menu → **APIs & Services → Library** → khujo **Google Drive API** → **Enable**

### Step B — OAuth Client banano
1. **APIs & Services → OAuth consent screen** → External → app nam dao, nijer email dao → Save
2. **Test users**-e giye **jotogula Gmail pool-e use korba, sob gula add koro**
   ⚠️ Ei step ta miss korle oi Gmail connect korte gele Google block korbe — sobcheye common vul eta
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
4. Type: **Web application**
5. **Authorized redirect URI**-te boshao: `http://127.0.0.1:8000/auth/callback`
   (pore server er thikana bodlale shei onujayi hobe, jemon `https://tomar-domain/auth/callback`)
6. Create → ekta **Client ID** ar **Client Secret** paba

### Step C — Panel-e boshao
1. Project folder-e `.env.example` file tar copy kore nam dao `.env`
2. `.env` khule boshao:
   ```
   GOOGLE_CLIENT_ID=ekhane-client-id
   GOOGLE_CLIENT_SECRET=ekhane-secret
   BASE_URL=http://127.0.0.1:8000
   ```
   ⚠️ **Secret ta karo shathe share korba na, screenshot-eo na**
3. Server restart koro

### Step D — Gmail gula connect koro
1. Panel-e **"+ Gmail Connect Koro"** chapo → Google login page asbe → ekta Gmail diye allow koro
2. Evabe **minimum 5 ta** Gmail ek ek kore connect koro (maximum limit nai)
3. 5 ta holei upload chalu hoye jabe

---

## 4. Server chalano

```bash
cd multi-drive-panel
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
```
Browser-e kholo: `http://127.0.0.1:8000` (nijer computer theke)
Phone theke dekhte chaile ek-i WiFi-te theke computer er IP diye: `http://192.168.x.x:8000`

**Mone rakho:** Panel cholbe tokhoni, jokhon ei server ta chalu thakbe. Sobsomoy chalu rakhte chaile pore ekta sosta VPS / Cinetra hosting er pashe bosate hobe — seta alada step.

---

## 5. Joruri kotha

- **Index database** holo `data/panel.db` — eta hariye gele Drive-e file thakleo khuje pawa jabe na. Majhe majhe ei file tar copy rekho.
- `data/secret.key` file ta token kholar chabi — eta o backup rekho, karo shathe share na.
- Google er niyom: onek free account diye storage barano Terms er biruddhe jete pare — nijer dayitte, personal/admin use-e rakha-i nirapod.
- Public streaming-e onek manush ekshathe dekhle Google quota block dite pare (agei bola chhilo).
