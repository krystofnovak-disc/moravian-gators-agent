"""
Generátor příspěvku na Facebook/Instagram pro Moravian Gators Nový Jičín.
Používá Claude API (claude-sonnet) a výsledky ze scraperů.
"""

import anthropic
import json
import os
import logging
from datetime import date

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Vzorové příspěvky klubu (pro zachování tónu a stylu)
# ---------------------------------------------------------------------------
EXAMPLE_POSTS = """
--- PŘÍKLAD 1 ---
Další víkend, další medaile!
Naši novojičínští Gatoři o uplynulém víkendu vyrazili hned na 3 různé turnaje. Nepočetná skupina ostřílených matadorů vycestovala až do dalekých Českých Budějovic, kde se zúčastnila druholigového turnaje Budweis Stromovka Open. Krásné umístění odtamdud přivezl náš předseda Kryštof Novák, který skončil na 3. místě v kategorii MPO. Kompletní výsledky jsou zde: https://idiscgolf.cz/turnaje/1193

V sobotu se konal také turnaj O ševcovo kopyto na DiscGolfParku Jižní Svahy ve Zlíně, kterého se zúčastnilo dalších 5 členů našeho klubu. Medaile brali hned dva.
MP40:
🥇 Jindrich Zavodny (spoluředitel turnajů NJDGT)
MA4:
🥇 Jakub Janíček (jeden z našich nejnovějších členů)
V losovačce se prosadil i náš Luky Španihel. Kompletní výsledky jsou zde: https://idiscgolf.cz/turnaje/1252

Hned 17člennou výpravu jsme vyslali na 2. turnaj Valašské discgolfové ligy, který se v neděli odehrál na Búřově. A ve 4 z vypsaných 5 kategorií jsme brali cenné kovy.
MPO:
🥇 Jakub Knápek
FPO:
🥇 Nikol Mikuláštík (vyhrála napůl s Bohdanem Bílkem i acepool)
🥈 Kristýna Jurčíková
MP40:
🥇 Radek Knápek
🥈 Silvestr Mikuláštík (navíc vyhrál i CTP)
MJ15:
🥇 Silvestr Mikuláštík mladší
Kompletní výsledky najdete zde: https://idiscgolf.cz/turnaje/1166

Všem členům mockrát děkujeme za parádní reprezentaci města Nový Jičín, našich partnerů i klubu samotného a těšíme se na další turnaje už tento víkend!
#discgolf #zijemeprodiscgolf #moraviangators

--- PŘÍKLAD 2 ---
Tento víkend naši členové reprezentovali Gatory a Město Nový Jičín hned ve třech různých krajích.
Sedmičlenná výprava v sobotu vyrazila na průzkum Valašského Mijas, turnaje Wallachian proDiscgolf.cz Tour na dočasném hřišti designovaném Silvestrem Mikuláštíkem. Nejvíce tam šla vidět naše mládež:
FPO
🥇 Nikol Mikuláštik
🥈 Michaela Mikuláštík
MJ18
🥇 Lukáš Španihel

Do dalekých Prachatic se vypravil náš předseda, aby tam udělal akademii a opět si uspořádal a vyhrál turnaj:
MPO
🥇 Kryštof Novák

V neděli se 7 našich členů zúčastnilo turnaje Discgolfové ligy Moravy a Slezska na ostravském DiscGolfParku sady Šporovnice a byli pořádně vidět:
MPO
🥇 Jakub Knápek
🥈 Petr Masník
🏅 František Trenz
MJ18
🥇 Lukáš Španihel

Moc všem děkujeme za reprezentaci a už se těšíme na další turnaje! Tento víkend některé z nás čeká druholigový turnaj Budweis Stromovky Open, část klubu vyráží i na Valašskou ligu na Búřově a taky na turnaj do Zlíns. Držte nám palce!
"""


class PostGenerator:
    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY není nastaven v prostředí.")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    def generate(self, tournament_results: list, saturday: date, sunday: date) -> str:
        """
        Vygeneruje příspěvek pro FB/Instagram na základě výsledků turnajů.

        Args:
            tournament_results: seznam turnajů s our_players výsledky
            saturday / sunday: datum uplynulého víkendu
        Returns:
            Text příspěvku jako string
        """
        results_text = self._format_results_for_prompt(tournament_results)
        sat_fmt = saturday.strftime("%-d. %-m. %Y")
        sun_fmt = sunday.strftime("%-d. %-m. %Y")

        prompt = f"""Jsi správce sociálních sítí discgolfového klubu Moravian Gators Nový Jičín.

Napiš příspěvek na Facebook a Instagram v češtině o výsledcích z uplynulého víkendu ({sat_fmt}–{sun_fmt}).

VÝSLEDKY TURNAJŮ:
{results_text}

POKYNY PRO PSANÍ:
- Piš neformálně a přátelsky – jako kamarád, ne novinář. Ale NEPŘEHÁNĚJ nadšení, buď přirozený.
- Lehká nadsázka je OK, ale nepřeháněj to s vykřičníky a superlativy.
- Každý turnaj má svůj vlastní odstavec s krátkým příběhem (kdo jel, kolik nás bylo).
- Výsledky uváděj dle kategorií (MPO, FPO, MA1, MA2, MA3, MA4, MP40, MJ15, MJ18 atd.)
- U hráčů na místě 1–3 použij medailové emoji: 🥇 1. místo, 🥈 2. místo, 🥉 3. místo
- PRAVIDLA PRO VÝPIS HRÁČŮ:
  * Turnaj S medailí: vypiš POUZE medailisty (1.–3. místo) a celkový počet Gatorů na turnaji. Ostatní jmenovitě nevypisuj. Celkový počet Gatorů uveď JEDNOU k celému turnaji, NE ke každé kategorii zvlášť.
  * Turnaj BEZ medaile: vypiš jmenovitě max. 3 nejlepší naše hráče a pak jen celkový počet.
  * Příspěvek NESMÍ být příliš dlouhý – buď stručný.
- Pokud mají dva nebo více hráčů STEJNÉ umístění NA MEDAILOVÉ POZICI (1.–3.), pravděpodobně hráli rozhoz. Označ to ⚠️ a poznámkou „(rozhoz – doplnit skutečné pořadí)". Rozhozy mimo medailové pozice neřeš.
- FORMÁTOVÁNÍ: Nepoužívej zbytečné prázdné řádky mezi odstavci. Odstavce odděluj jedním řádkem, ne dvěma. Cílem je kompaktní příspěvek.
- Pokud hráč vyhrál CTP, acepool nebo losovačku, zmíň to v závorce.
- Pokud hráč má roli (předseda, spoluředitel), použij ji pro osobní charakteristiku.
- Pokud hráč má note (starší/mladší, nejnovější člen), zohledni to.
- Pokud byli naši hráči na PDGA turnaji mimo ČR, zdůrazni mezinárodní start.
- Zakonči poděkováním za reprezentaci klubu a města Nový Jičín a teaserem na příští víkend.
- Na konec přidej: #discgolf #zijemeprodiscgolf #moraviangators
- Nepiš žádný úvodní komentář, jen samotný text příspěvku.
- DŮLEŽITÉ: Dej velký pozor na českou gramatiku – nevynechávej slova, kontroluj skloňování a shodu.
- POČTY HRÁČŮ: U každého turnaje jsou uvedeny přesné hodnoty POČET NAŠICH HRÁČŮ NA TURNAJI a POČET MEDAILÍ. Tyto číselné údaje jsou 100% správné – PŘEPIŠ JE DOSLOVA do textu, NEPOČÍTEJ je sám. Nikdy nepiš jiné číslo, než které je uvedeno v datech.

VZOROVÉ PŘÍSPĚVKY (pro inspiraci stylem, ne kopírování):
{EXAMPLE_POSTS}

Napiš příspěvek:"""

        logger.info(f"Volám Claude API (model: {self.model})…")
        message = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )

        post = message.content[0].text.strip()
        logger.info("Příspěvek úspěšně vygenerován.")
        return post

    # ------------------------------------------------------------------
    # Formátování výsledků pro prompt
    # ------------------------------------------------------------------

    def _format_results_for_prompt(self, tournament_results: list) -> str:
        """Převede seznam turnajů do čitelného textu pro prompt."""
        if not tournament_results:
            return "(Žádné výsledky nebyly nalezeny.)"

        lines = []
        for t in tournament_results:
            players = t.get("our_players", [])
            total = len(players)
            medalists = sum(1 for p in players if p.get("place") and p["place"] <= 3)
            lines.append(f"\nTURNAJ: {t['name']}")
            lines.append(f"  Datum: {t.get('date', 'neznámé')}")
            lines.append(f"  Odkaz: {t.get('url', '')}")
            lines.append(f"  Zdroj: {t.get('source', '')}")
            lines.append(f"  POČET NAŠICH HRÁČŮ NA TURNAJI: {total}")
            lines.append(f"  POČET MEDAILÍ (1.–3. místo): {medalists}")
            lines.append("  Naši hráči:")

            # Seskupíme podle divize
            by_div: dict[str, list] = {}
            for player in t.get("our_players", []):
                div = player.get("division") or "Neznámá kategorie"
                by_div.setdefault(div, []).append(player)

            for div, players in by_div.items():
                lines.append(f"    {div}:")
                for p in sorted(players, key=lambda x: (x.get("place") or 999)):
                    place = p.get("place")
                    place_str = f"{place}. místo" if place else "účast"
                    name = f"{p['first_name']} {p['last_name']}"
                    role = f" [{p['role']}]" if p.get("role") else ""
                    note = f" ({p['note']})" if p.get("note") else ""
                    lines.append(f"      - {place_str}: {name}{role}{note}")

        return "\n".join(lines)
