#!/usr/bin/env bash
# cutover_snapshot.sh — снимок mutable-state ПЕРЕД боевым cutover (CTO-ревью C1+C2).
#
# Зачем: страховка перед необратимой пересадкой. При copy-on-cutover (C5) old-state
# в /root/content-bot своим каталогом неприкосновенен, но snapshot защищает от
# случайной записи нового бота в old-пути (если MAKSIM_*/пути не переопределены, R4)
# и даёт базу для restore-режима rollback_panferov.sh.
#
# Снимаем только КРИТИЧНЫЕ МЕЛКИЕ mutable-файлы (pending/billing/токены/сессии/stats).
# projects/ (952MB) и assets/ (118MB) НЕ снимаем — большие, copy-on-cutover их в
# old не трогает; при нужде бэкапятся отдельно (feedback_daily_backups зеркалит их).
#
# Запуск (на сервере A под root): bash cutover_snapshot.sh [/root/content-bot]
set -euo pipefail

SRC="${1:-/root/content-bot}"
SNAP="/root/cutover_snapshots/panferov_$(date +%Y%m%d_%H%M%S)"

echo "[snapshot] источник: $SRC"
echo "[snapshot] назначение: $SNAP"

# ── C2 DRAIN: убедиться, что нет активных писателей state ─────────────────────
echo "[drain] проверка активных тяжёлых процессов..."
BUSY="$(pgrep -af 'ffmpeg|remotion|hyperframes|puppeteer|yt-dlp|claude -p' | grep -v 'pgrep' || true)"
if [ -n "$BUSY" ]; then
  echo "[drain] ⚠️ АКТИВНЫЕ задачи — НЕ делаю snapshot (state может меняться):"
  echo "$BUSY"
  echo "[drain] дождись завершения или отмени, потом повтори."
  exit 1
fi
echo "[drain] нет активных тяжёлых процессов — ok"

# Открытые дескрипторы на критичные файлы (другой писатель?)
for f in "$SRC/pending.json" "$SRC/billing/billing.db"; do
  [ -f "$f" ] || continue
  if command -v lsof >/dev/null 2>&1 && lsof "$f" >/dev/null 2>&1; then
    echo "[drain] ⚠️ $f открыт другим процессом:"; lsof "$f" || true
    echo "[drain] останови бота перед snapshot."; exit 1
  fi
done

# SQLite integrity (битая база = плохой снимок)
if [ -f "$SRC/billing/billing.db" ]; then
  python3 -c "import sqlite3;r=sqlite3.connect('$SRC/billing/billing.db').execute('PRAGMA integrity_check').fetchone()[0];print('[drain] billing integrity:',r);exit(0 if r=='ok' else 1)"
fi

# ── C1 SNAPSHOT: копируем критичные мелкие mutable-файлы ──────────────────────
mkdir -p "$SNAP"
# по одному, чтобы отсутствие необязательного не валило set -e
copy_if() { [ -e "$1" ] && cp -a "$1" "$SNAP/" && echo "[snapshot]  + $(basename "$1")" || true; }

copy_if "$SRC/pending.json"
copy_if "$SRC/.env"
# billing — SQLite через .backup (консистентно даже при открытой базе)
if [ -f "$SRC/billing/billing.db" ]; then
  mkdir -p "$SNAP/billing"
  python3 -c "import sqlite3;s=sqlite3.connect('$SRC/billing/billing.db');d=sqlite3.connect('$SNAP/billing/billing.db');s.backup(d);d.close();s.close();print('[snapshot]  + billing/billing.db (.backup)')"
fi
# OAuth-токены + Telethon-сессии (G1)
for f in "$SRC"/*token*.json "$SRC"/*.session "$SRC"/stats_history.json "$SRC"/pub_calendar.json; do
  copy_if "$f"
done

# ── MANIFEST (checksum для проверки целостности и restore) ────────────────────
( cd "$SNAP" && find . -type f -print0 | sort -z | xargs -0 sha256sum > MANIFEST.sha256 )
echo "[snapshot] manifest: $SNAP/MANIFEST.sha256 ($(wc -l < "$SNAP/MANIFEST.sha256") файлов)"

echo "[snapshot] ✅ ГОТОВО: $SNAP"
echo "[snapshot] restore: bash rollback_panferov.sh --restore-state $SNAP"
echo "$SNAP"   # путь в stdout — для подстановки в rollback
