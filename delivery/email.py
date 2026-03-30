"""
Odeslání vygenerovaného příspěvku e-mailem přes Gmail SMTP.

Nastavení:
  1. Zapni 2-faktorové ověření v Google účtu
  2. Vygeneruj App Password: https://myaccount.google.com/apppasswords
  3. Ulož do .env jako GMAIL_ADDRESS a GMAIL_APP_PASSWORD
"""

from __future__ import annotations

import smtplib
import os
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)


class EmailSender:
    def __init__(self):
        self.smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.gmail_address = os.getenv("GMAIL_ADDRESS")
        self.app_password = os.getenv("GMAIL_APP_PASSWORD")
        self.recipient = os.getenv("RECIPIENT_EMAIL", "vybor@moraviangators.cz")

    def send(self, post_text: str, saturday: date, sunday: date,
             tournament_results: list | None = None) -> None:
        """Odešle příspěvek jako e-mail ke schválení."""
        if not self.gmail_address or not self.app_password:
            raise ValueError(
                "Chybí Gmail přihlašovací údaje. "
                "Nastav GMAIL_ADDRESS a GMAIL_APP_PASSWORD v souboru .env"
            )

        sat = saturday.strftime("%-d. %-m.")
        sun = sunday.strftime("%-d. %-m. %Y")
        subject = f"🥏 Moravian Gators – příspěvek víkendu {sat}–{sun} [ke schválení]"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.gmail_address
        msg["To"] = self.recipient

        # Čistý text (tabulka + příspěvek)
        plain = self._build_plain(post_text, tournament_results)
        msg.attach(MIMEText(plain, "plain", "utf-8"))
        # HTML verze
        html = self._to_html(post_text, saturday, sunday, tournament_results)
        msg.attach(MIMEText(html, "html", "utf-8"))

        try:
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.login(self.gmail_address, self.app_password)
                server.sendmail(self.gmail_address, self.recipient, msg.as_string())
            logger.info(f"E-mail odeslán na {self.recipient}")
        except Exception as e:
            logger.error(f"Odeslání e-mailu selhalo: {e}")
            raise

    # ------------------------------------------------------------------
    # Plaintext verze
    # ------------------------------------------------------------------

    def _build_plain(self, post_text: str,
                     tournament_results: list | None) -> str:
        """Sestaví plaintext verzi e-mailu (tabulka + příspěvek)."""
        parts = []
        if tournament_results:
            parts.append("PŘEHLED VÝSLEDKŮ ČLENŮ MGNJ")
            parts.append("=" * 40)
            for t in tournament_results:
                players = t.get("our_players", [])
                parts.append(
                    f"\n{t['name']} ({t.get('date', '')}) "
                    f"– {len(players)} Gator{'ů' if len(players) != 1 else ''}"
                )
                parts.append(t.get("url", ""))
                for p in sorted(players, key=lambda x: (x.get("place") or 999)):
                    name = f"{p['first_name']} {p['last_name']}"
                    div = p.get("division") or "?"
                    place = f"{p['place']}." if p.get("place") else "–"
                    parts.append(f"  {name:<25} {div:<8} {place}")
            parts.append("\n" + "=" * 40)
            parts.append("\nNÁVRH PŘÍSPĚVKU NA FB/INSTAGRAM:\n")
        parts.append(post_text)
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # HTML verze
    # ------------------------------------------------------------------

    def _results_table_html(self, tournament_results: list) -> str:
        """Sestaví HTML tabulku s přehledem výsledků."""
        rows = []
        for t in tournament_results:
            players = t.get("our_players", [])
            count = len(players)
            url = t.get("url", "")
            name = t["name"]
            date_str = t.get("date", "")
            link = f'<a href="{url}" style="color: #fff; text-decoration: underline;">{name}</a>' if url else name

            # Řádek s názvem turnaje (přes celou šířku)
            rows.append(
                f'<tr>'
                f'<td colspan="3" style="background: #2e7d32; color: #fff; '
                f'padding: 8px 12px; font-weight: bold; font-size: 14px;">'
                f'{link} ({date_str}) '
                f'<span style="font-weight: normal; opacity: 0.85;">'
                f'– {count} Gator{"ů" if count != 1 else ""}</span>'
                f'</td>'
                f'</tr>'
            )

            # Řádky hráčů
            for i, p in enumerate(sorted(players, key=lambda x: (x.get("place") or 999))):
                name_str = f"{p['first_name']} {p['last_name']}"
                note = p.get("note", "")
                if note:
                    name_str += f" ({note})"
                div = p.get("division") or "–"
                place = p.get("place")
                if place == 1:
                    place_str = "🥇 1."
                elif place == 2:
                    place_str = "🥈 2."
                elif place == 3:
                    place_str = "🥉 3."
                elif place:
                    place_str = f"{place}."
                else:
                    place_str = "–"

                bg = "#f9f9f9" if i % 2 == 0 else "#fff"
                rows.append(
                    f'<tr style="background: {bg};">'
                    f'<td style="padding: 4px 12px; font-size: 14px;">{name_str}</td>'
                    f'<td style="padding: 4px 12px; font-size: 14px; text-align: center;">{div}</td>'
                    f'<td style="padding: 4px 12px; font-size: 14px; text-align: center;">{place_str}</td>'
                    f'</tr>'
                )

        return (
            '<table style="width: 100%; border-collapse: collapse; '
            'margin-bottom: 24px; border: 1px solid #ddd; border-radius: 6px; '
            'overflow: hidden;">\n'
            + "\n".join(rows)
            + "\n</table>"
        )

    def _to_html(self, post_text: str, saturday: date, sunday: date,
                 tournament_results: list | None = None) -> str:
        """Přeformátuje příspěvek jako HTML e-mail."""
        sat = saturday.strftime("%-d. %-m.")
        sun = sunday.strftime("%-d. %-m. %Y")

        # Převod textu příspěvku na HTML – nahradíme prázdné řádky za
        # odstavcový oddělovač, ostatní \n za <br>
        paragraphs = post_text.split("\n\n")
        body_html = "</p>\n<p style=\"margin: 0 0 10px 0;\">".join(
            p.replace("\n", "<br>") for p in paragraphs
        )
        body_html = f'<p style="margin: 0 0 10px 0;">{body_html}</p>'

        # Tabulka výsledků (pokud existují data)
        table_html = ""
        if tournament_results:
            table_html = (
                '<h3 style="margin: 0 0 10px 0; font-size: 16px; color: #1b5e20;">'
                'Přehled výsledků členů MGNJ</h3>\n'
                + self._results_table_html(tournament_results)
                + '\n<h3 style="margin: 0 0 10px 0; font-size: 16px; color: #1b5e20;">'
                'Návrh příspěvku na FB/Instagram</h3>\n'
            )

        return f"""<!DOCTYPE html>
<html lang="cs">
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; max-width: 640px; margin: 0 auto; padding: 24px; color: #222;">

  <div style="background: #1b5e20; padding: 16px 24px; border-radius: 8px; margin-bottom: 16px;">
    <h2 style="color: #fff; margin: 0; font-size: 18px;">
      🥏 Moravian Gators – výsledky víkendu {sat}–{sun}
    </h2>
  </div>

  <div style="background: #fff8e1; border-left: 4px solid #f9a825; padding: 10px 16px;
              border-radius: 4px; margin-bottom: 16px; font-size: 13px; color: #555;">
    <strong>Automaticky vygenerováno</strong> – zkontroluj text a případně uprav před zveřejněním na FB/Instagramu.
  </div>

  {table_html}

  <div style="line-height: 1.5; font-size: 15px;">
{body_html}
  </div>

  <hr style="margin: 24px 0; border: none; border-top: 1px solid #eee;">
  <p style="color: #aaa; font-size: 12px; margin: 0;">
    Vygeneroval Moravian Gators Agent · každé pondělí v 8:00
  </p>

</body>
</html>"""
