#!/usr/bin/env bash
# rollback_panferov.sh — откат Phase 3 cutover (panferov: core → legacy).
#
# Назначение: вернуть panferov на старый рабочий unit, если после пересадки на
# contentbot-core что-то пошло не так. По CTO-ревью (C4/I8): откат должен быть
# написан И ПРОГНАН на ТЕСТ-токене ДО боевого cutover — не «на ходу».
#
# ⚠️ Сервер A (65.21.154.237) держит И content-bot, И Nox — оба Python.
# Поэтому проверки строго unit-level (systemctl is-active / MainPID),
# НИКАКОГО `pgrep -f bot.py` (поймает не тот процесс).
#
# Инвариант: один Telegram-токен = один процесс. Новый ОБЯЗАН остановиться
# (inactive + MainPID=0) ДО старта старого, иначе два процесса на одном токене.
#
# Запуск:
#   bash rollback_panferov.sh                          — откат только unit
#   bash rollback_panferov.sh --restore-state <SNAP>   — + восстановить mutable-state
#       из snapshot (C1: если новый успел записать в old-пути). SNAP = каталог
#       cutover_snapshot.sh. Manifest сверяется перед restore.
set -euo pipefail

NEW_UNIT="contentbot-core"
OLD_UNIT="content-bot"
OLD_DIR="/root/content-bot"
OLD_LOG="$OLD_DIR/bot.log"

RESTORE_SNAP=""
if [ "${1:-}" = "--restore-state" ]; then
  RESTORE_SNAP="${2:-}"
  [ -d "$RESTORE_SNAP" ] || { echo "[rollback] ❌ snapshot не найден: $RESTORE_SNAP"; exit 1; }
fi

echo "[rollback] стоп нового юнита: $NEW_UNIT"
systemctl stop "$NEW_UNIT" || true

# Ждём фактической остановки нового (до ~10 c): inactive/failed И MainPID=0.
stopped=0
for _ in $(seq 1 10); do
  state="$(systemctl is-active "$NEW_UNIT" 2>/dev/null || true)"
  mainpid="$(systemctl show "$NEW_UNIT" -p MainPID --value 2>/dev/null || echo 0)"
  if { [ "$state" = "inactive" ] || [ "$state" = "failed" ]; } && [ "$mainpid" = "0" ]; then
    stopped=1
    break
  fi
  echo "[rollback] жду остановки $NEW_UNIT (state=$state pid=$mainpid)..."
  sleep 1
done

if [ "$stopped" -ne 1 ]; then
  echo "[rollback] ❌ $NEW_UNIT не остановился за 10 c — НЕ стартую старый (риск двух процессов на токене)."
  echo "[rollback] разберись вручную: systemctl status $NEW_UNIT ; systemctl show $NEW_UNIT -p MainPID"
  exit 1
fi

# C1: восстановить mutable-state из snapshot ДО старта старого (если задан) —
# страховка против записи нового бота в old-пути.
if [ -n "$RESTORE_SNAP" ]; then
  echo "[rollback] restore state из $RESTORE_SNAP → $OLD_DIR (сверка manifest)"
  if ! ( cd "$RESTORE_SNAP" && sha256sum -c MANIFEST.sha256 --quiet ); then
    echo "[rollback] ❌ manifest snapshot не сошёлся — НЕ восстанавливаю (битый снимок)."
    exit 1
  fi
  [ -f "$RESTORE_SNAP/pending.json" ] && cp -a "$RESTORE_SNAP/pending.json" "$OLD_DIR/" && echo "[rollback]  + pending.json"
  [ -f "$RESTORE_SNAP/billing/billing.db" ] && cp -a "$RESTORE_SNAP/billing/billing.db" "$OLD_DIR/billing/" && echo "[rollback]  + billing/billing.db"
  for f in "$RESTORE_SNAP"/*token*.json "$RESTORE_SNAP"/*.session; do
    [ -e "$f" ] && cp -a "$f" "$OLD_DIR/" && echo "[rollback]  + $(basename "$f")"
  done
  echo "[rollback] state восстановлен из snapshot"
fi

echo "[rollback] старт старого юнита (legacy = путь отката): $OLD_UNIT"
systemctl start "$OLD_UNIT"
sleep 4

state="$(systemctl is-active "$OLD_UNIT" 2>/dev/null || true)"
if [ "$state" = "active" ]; then
  echo "[rollback] $OLD_UNIT active — проверяю стабильность (критик Q4)..."
  # Не «стартанул = ок»: подождать и проверить что не падает/нет конфликта токена.
  sleep 10
  state2="$(systemctl is-active "$OLD_UNIT" 2>/dev/null || true)"
  if [ "$state2" != "active" ]; then
    echo "[rollback] ❌ $OLD_UNIT упал в первые 10 c (state=$state2) — РУЧНОЕ вмешательство!"
    journalctl -u "$OLD_UNIT" -n 50 --no-pager 2>/dev/null | grep -iE "traceback|critical|conflict|unauthorized" | tail -10 || true
    exit 1
  fi
  if journalctl -u "$OLD_UNIT" -n 80 --no-pager 2>/dev/null | grep -qiE "traceback|conflict|unauthorized|terminated by other"; then
    echo "[rollback] ⚠️ $OLD_UNIT active, но в журнале есть подозрительное (polling-конфликт/ошибка) — проверь:"
    journalctl -u "$OLD_UNIT" -n 80 --no-pager 2>/dev/null | grep -iE "traceback|conflict|unauthorized|terminated by other" | tail -8 || true
  fi
  echo "[rollback] ✅ $OLD_UNIT стабилен 10 c — откат завершён."
  echo "[rollback] последние строки лога (без PTB-warning):"
  grep -vE 'PTBUserWarning|per_' "$OLD_LOG" 2>/dev/null | tail -8 || true
  echo "[rollback] ПРОВЕРЬ вручную: /start боту panferov + один callback."
  exit 0
fi

echo "[rollback] ❌ $OLD_UNIT НЕ поднялся (state=$state) — РУЧНОЕ вмешательство!"
echo "[rollback] journalctl -u $OLD_UNIT -n 50 --no-pager"
exit 1
