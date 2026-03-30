#!/bin/bash
# ============================================================
# Nastavení cron jobu – Moravian Gators Agent
# Spouštěj každé pondělí v 8:00
# ============================================================
# Spuštění: chmod +x setup_cron.sh && ./setup_cron.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$(which python3)"
CRON_LOG="$SCRIPT_DIR/cron.log"

# Cron výraz: 0 8 * * 1 = každé pondělí v 8:00
CRON_JOB="0 8 * * 1 cd \"$SCRIPT_DIR\" && \"$PYTHON_BIN\" main.py >> \"$CRON_LOG\" 2>&1"

echo "📋 Instaluji cron job..."
echo "   Skript: $SCRIPT_DIR/main.py"
echo "   Python: $PYTHON_BIN"
echo "   Log:    $CRON_LOG"
echo ""

# Přidáme do crontab (pokud tam ještě není)
( crontab -l 2>/dev/null | grep -v "moravian-gators-agent"; echo "$CRON_JOB" ) | crontab -

echo "✅ Cron job nastaven. Ověření:"
crontab -l | grep "moravian-gators"
echo ""
echo "📝 Spuštění testu (dry run):"
echo "   cd $SCRIPT_DIR && python3 main.py --dry-run"
echo ""
echo "📅 Spuštění pro konkrétní víkend:"
echo "   python3 main.py --date 2026-03-14"
