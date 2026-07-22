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


def _hraci(n: int) -> str:
    """České skloňování: 1 hráč / 2–4 hráči / 5+ hráčů."""
    if n == 1:
        return "hráč"
    if 2 <= n <= 4:
        return "hráči"
    return "hráčů"


class EmailSender:
    def __init__(self):
        self.smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.gmail_address = os.getenv("GMAIL_ADDRESS")
        self.app_password = os.getenv("GMAIL_APP_PASSWORD")
        self.recipient = os.getenv("RECIPIENT_EMAIL", "vybor@moraviangators.cz")

    def send(self, post_text: str | None, saturday: date, sunday: date,
             tournament_results: list | None = None,
             warnings: list | None = None,
             club: dict | None = None) -> None:
        """Odešle e-mail s výsledky víkendu.

        post_text None → summary-only režim (jen tabulka výsledků, bez
        návrhu příspěvku, předmět bez "ke schválení").
        club: konfigurace klubu (name, recipient_email, dashboard_url, …).
              Určuje příjemce a branding. None → výchozí (mgnj).
        warnings: varování o neúplných datech (banner + značka v předmětu).
        """
        if club is None:
            from clubs import get_club
            club = get_club()
        # recipient_email může být string nebo seznam adres.
        raw_recipient = club.get("recipient_email") or self.recipient
        recipients = [raw_recipient] if isinstance(raw_recipient, str) else list(raw_recipient)
        short = club.get("short_name") or club.get("name", "")
        emoji = club.get("emoji", "🥏")

        if not self.gmail_address or not self.app_password:
            raise ValueError(
                "Chybí Gmail přihlašovací údaje. "
                "Nastav GMAIL_ADDRESS a GMAIL_APP_PASSWORD v souboru .env"
            )

        sat = saturday.strftime("%-d. %-m.")
        sun = sunday.strftime("%-d. %-m. %Y")
        if post_text:
            subject = f"{emoji} {short} – příspěvek víkendu {sat}–{sun} [ke schválení]"
        else:
            subject = f"{emoji} {short} – souhrn výsledků víkendu {sat}–{sun}"
        if warnings:
            subject = f"⚠️ NEÚPLNÁ DATA – {subject}"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.gmail_address
        msg["To"] = ", ".join(recipients)

        # Čistý text (banner + tabulka + příspěvek)
        plain = self._build_plain(post_text, tournament_results, warnings, club)
        msg.attach(MIMEText(plain, "plain", "utf-8"))
        # HTML verze
        html = self._to_html(post_text, saturday, sunday, tournament_results, warnings, club)
        msg.attach(MIMEText(html, "html", "utf-8"))

        try:
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.login(self.gmail_address, self.app_password)
                server.sendmail(self.gmail_address, recipients, msg.as_string())
            logger.info(f"E-mail odeslán na {', '.join(recipients)}")
        except Exception as e:
            logger.error(f"Odeslání e-mailu selhalo: {e}")
            raise

    # ------------------------------------------------------------------
    # Plaintext verze
    # ------------------------------------------------------------------

    def _build_plain(self, post_text: str | None,
                     tournament_results: list | None,
                     warnings: list | None = None,
                     club: dict | None = None) -> str:
        """Sestaví plaintext verzi e-mailu (banner + tabulka + [příspěvek])."""
        short = (club or {}).get("short_name") or "klubu"
        parts = []
        if warnings:
            parts.append("!" * 56)
            parts.append("!!  POZOR: NEPODAŘILO SE NAČÍST VŠECHNA DATA")
            parts.append("!!  Výsledky níže NEMUSÍ být kompletní:")
            for w in warnings:
                parts.append(f"!!    • {w}")
            parts.append("!" * 56)
            parts.append("")
        if tournament_results:
            parts.append(f"PŘEHLED VÝSLEDKŮ – {short}")
            parts.append("=" * 40)
            for t in tournament_results:
                players = t.get("our_players", [])
                parts.append(
                    f"\n{t['name']} ({t.get('date', '')}) "
                    f"– {len(players)} {_hraci(len(players))}"
                )
                parts.append(t.get("url", ""))
                for p in sorted(players, key=lambda x: (x.get("place") or 999)):
                    name = f"{p['first_name']} {p['last_name']}"
                    div = p.get("division") or "?"
                    place = f"{p['place']}." if p.get("place") else "–"
                    parts.append(f"  {name:<25} {div:<8} {place}")
            parts.append("\n" + "=" * 40)
        if post_text:
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
                f'– {count} {_hraci(count)}</span>'
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

    def _warning_banner_html(self, warnings: list | None) -> str:
        """Výrazný červený banner nahoře e-mailu při neúplných datech."""
        if not warnings:
            return ""
        items = "\n".join(
            f'<li style="margin: 2px 0;">{w}</li>' for w in warnings
        )
        return (
            '<div style="background: #b71c1c; border: 2px solid #7f0000; '
            'padding: 14px 20px; border-radius: 8px; margin-bottom: 16px; color: #fff;">\n'
            '  <div style="font-size: 17px; font-weight: bold; margin-bottom: 6px;">'
            '⚠️ POZOR: nepodařilo se načíst všechna data</div>\n'
            '  <div style="font-size: 14px; margin-bottom: 6px;">'
            'Výsledky níže <strong>nemusí být kompletní</strong>:</div>\n'
            f'  <ul style="margin: 0; padding-left: 20px; font-size: 14px;">\n{items}\n  </ul>\n'
            '</div>'
        )

    def _to_html(self, post_text: str | None, saturday: date, sunday: date,
                 tournament_results: list | None = None,
                 warnings: list | None = None,
                 club: dict | None = None) -> str:
        """Přeformátuje výsledky (a volitelně příspěvek) jako HTML e-mail."""
        club = club or {}
        short = club.get("short_name") or club.get("name", "")
        emoji = club.get("emoji", "🥏")
        dashboard_url = club.get("dashboard_url")

        sat = saturday.strftime("%-d. %-m.")
        sun = sunday.strftime("%-d. %-m. %Y")

        # Blok návrhu příspěvku – jen kluby, které ho generují
        post_block = ""
        intro_note = ""
        if post_text:
            paragraphs = post_text.split("\n\n")
            body_html = "</p>\n<p style=\"margin: 0 0 10px 0;\">".join(
                p.replace("\n", "<br>") for p in paragraphs
            )
            body_html = f'<p style="margin: 0 0 10px 0;">{body_html}</p>'
            post_block = (
                '<h3 style="margin: 0 0 10px 0; font-size: 16px; color: #1b5e20;">'
                'Návrh příspěvku na FB/Instagram</h3>\n'
                f'<div style="line-height: 1.5; font-size: 15px;">\n{body_html}\n</div>'
            )
            intro_note = (
                '<div style="background: #fff8e1; border-left: 4px solid #f9a825; '
                'padding: 10px 16px; border-radius: 4px; margin-bottom: 16px; '
                'font-size: 13px; color: #555;">\n'
                '<strong>Automaticky vygenerováno</strong> – zkontroluj text a '
                'případně uprav před zveřejněním na FB/Instagramu.\n</div>'
            )

        # Tabulka výsledků
        table_html = ""
        if tournament_results:
            table_html = (
                f'<h3 style="margin: 0 0 10px 0; font-size: 16px; color: #1b5e20;">'
                f'Přehled výsledků – {short}</h3>\n'
                + self._results_table_html(tournament_results)
            )

        dashboard_block = ""
        if dashboard_url:
            dashboard_block = (
                '<div style="background: #e8edf7; padding: 12px 16px; border-radius: 6px; '
                'margin-top: 16px; text-align: center;">\n'
                f'  <a href="{dashboard_url}" style="color: #192f6b; font-weight: bold; '
                'font-size: 14px; text-decoration: none;">📊 Online databáze výsledků</a>\n'
                '</div>'
            )

        return f"""<!DOCTYPE html>
<html lang="cs">
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; max-width: 640px; margin: 0 auto; padding: 24px; color: #222;">

  <div style="background: #1b5e20; padding: 16px 24px; border-radius: 8px; margin-bottom: 16px;">
    <h2 style="color: #fff; margin: 0; font-size: 18px;">
      {emoji} {short} – výsledky víkendu {sat}–{sun}
    </h2>
  </div>

  {self._warning_banner_html(warnings)}

  {intro_note}

  {table_html}

  {post_block}

  {dashboard_block}

  <hr style="margin: 24px 0; border: none; border-top: 1px solid #eee;">
  <p style="color: #aaa; font-size: 12px; margin: 0;">
    Vygeneroval {short} Agent · každé pondělí
  </p>

</body>
</html>"""
