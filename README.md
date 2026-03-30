# 🥏 Moravian Gators – Tournament Results Agent

Automatický agent, který každé pondělí v 8:00 zkontroluje výsledky
discgolfových turnajů z uplynulého víkendu a pošle ti hotový příspěvek
na Facebook/Instagram e-mailem ke schválení.

---

## Jak to funguje

```
Každé pondělí 8:00
       │
       ▼
  Scraping idiscgolf.cz ──┐
  Scraping pdga.com       ├──▶  Filtrování dle seznamu členů
                          │
                          ▼
                   Claude API generuje příspěvek
                   (v tónu a stylu Moravian Gators)
                          │
                          ▼
                   E-mail na krystof.novak@gnj.cz
                   "ke schválení před zveřejněním"
```

---

## Požadavky

- **Python 3.11+** (zkontroluj: `python3 --version`)
- **Claude Code** nainstalovaný (viz https://claude.ai/code)
- Gmail účet s App Password
- Anthropic API klíč (https://console.anthropic.com)

---

## Instalace (jednorázové nastavení)

### 1. Naklonuj / zkopíruj projekt

```bash
# Zkopíruj složku moravian-gators-agent kamkoliv na svůj počítač
# Například:
cp -r moravian-gators-agent ~/Documents/gators-agent
cd ~/Documents/gators-agent
```

### 2. Vytvoř virtuální prostředí a nainstaluj závislosti

```bash
python3 -m venv venv
source venv/bin/activate          # macOS/Linux
# nebo: venv\Scripts\activate     # Windows

pip install -r requirements.txt
```

### 3. Nastav přihlašovací údaje

```bash
cp .env.example .env
# Otevři .env v editoru a vyplň:
nano .env   # nebo: open .env, notepad .env, code .env
```

Co potřebuješ vyplnit:

| Proměnná | Kde ji získáš |
|---|---|
| `ANTHROPIC_API_KEY` | https://console.anthropic.com/settings/keys |
| `GMAIL_ADDRESS` | Tvůj Gmail (např. `moraviangators@gmail.com`) |
| `GMAIL_APP_PASSWORD` | Viz níže |
| `RECIPIENT_EMAIL` | Kam chceš posílat příspěvky (default: `krystof.novak@gnj.cz`) |

**Jak získat Gmail App Password:**
1. Přihlas se do https://myaccount.google.com
2. Security → 2-Step Verification (musí být zapnuté)
3. Security → App passwords
4. Vyber: Mail + Other (název: "GatorsAgent")
5. Zkopíruj 16místné heslo do `.env`

### 4. Otestuj (dry run – bez generování a e-mailu)

```bash
python3 main.py --dry-run
```

Výstup ukáže, jaké turnaje by agent našel pro minulý víkend.

### 5. Otestuj plné spuštění

```bash
# Test na konkrétní víkend (doplň datum soboty)
python3 main.py --date 2026-03-14
```

Pošle skutečný e-mail na `RECIPIENT_EMAIL`.

### 6. Nastav automatické spouštění (cron)

```bash
chmod +x setup_cron.sh
./setup_cron.sh
```

Cron job se spustí automaticky každé pondělí v 8:00.

**Ověření:**
```bash
crontab -l | grep gators
```

---

## Aktualizace seznamu hráčů

Otevři `config/players.json` a přidej/uprav hráče dle potřeby.

Formát záznamu:
```json
{
  "first_name": "Jméno",
  "last_name": "Příjmení",
  "cadg": 1234,
  "pdga": 56789,
  "role": "popis role (volitelné, např. 'předseda klubu')",
  "note": "poznámka (volitelné, např. 'mladší')"
}
```

Pole `role` a `note` ovlivňují, jak Claude o hráči napíše v příspěvku.

---

## Řešení problémů

### Scraper nic nenajde

1. Spusť s `--dry-run` a zkontroluj log (`gators_agent.log`)
2. Otevři manuálně `https://idiscgolf.cz/turnaje` a ověř, zda se stránka načte

**Pokud idiscgolf.cz nebo pdga.com renderuje obsah JavaScriptem:**

```bash
pip install playwright
playwright install chromium
```

Pak v `scrapers/idiscgolf.py` nahraď `requests.Session().get(url)` za Playwright:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto(url)
    page.wait_for_load_state("networkidle")
    html = page.content()
    browser.close()
```

### E-mail se neodešle

- Zkontroluj `GMAIL_ADDRESS` a `GMAIL_APP_PASSWORD` v `.env`
- Ujisti se, že máš zapnuté 2FA v Google účtu
- Zkus: `python3 -c "from delivery.email import EmailSender; print('OK')"`

### Příspěvek má špatný tón

Otevři `generator/post.py` a uprav sekci `EXAMPLE_POSTS` nebo `prompt`.

---

## Struktura projektu

```
moravian-gators-agent/
├── config/
│   └── players.json          ← databáze členů klubu
├── scrapers/
│   ├── idiscgolf.py          ← scraper idiscgolf.cz
│   └── pdga.py               ← scraper pdga.com
├── generator/
│   └── post.py               ← generátor příspěvku (Claude API)
├── delivery/
│   └── email.py              ← odeslání e-mailem
├── output/                   ← automaticky vytvořená složka s výstupy
│   ├── results_YYYY-MM-DD_YYYY-MM-DD.json
│   └── post_YYYY-MM-DD_YYYY-MM-DD.txt
├── main.py                   ← hlavní skript
├── requirements.txt
├── .env.example              ← šablona pro přihlašovací údaje
├── .env                      ← TVOJE ÚDAJE (negit!)
├── setup_cron.sh             ← nastavení cron jobu
└── gators_agent.log          ← log spouštění
```

---

## Bezpečnost

- Soubor `.env` **nikdy nesdílej** ani necommituj do Gitu
- Pokud používáš Git: přidej `.env` do `.gitignore`
- App Password pro Gmail lze kdykoliv zrušit v nastavení Google účtu

---

*Vytvořeno pro Moravian Gators Nový Jičín · agent běží na Claude Sonnet 4.6*
