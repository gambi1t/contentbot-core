#!/usr/bin/env bash
# cutover_status.sh — быстрый снимок состояния бота для диагностики (CTO-ревью N1).
# «Очень помогает в панике»: одной командой — что активно, какой tenant/commit/strict.
#
# Запуск на сервере: bash cutover_status.sh [unit] [dir]
#   unit — systemd-юнит (по умолч. contentbot-core)
#   dir  — рабочий каталог (по умолч. /root/contentbot-core)
set -uo pipefail

UNIT="${1:-contentbot-core}"
DIR="${2:-/root/contentbot-core}"

echo "════════ cutover status: $UNIT ════════"

echo "── unit ──"
echo "is-active : $(systemctl is-active "$UNIT" 2>/dev/null || echo '?')"
echo "MainPID   : $(systemctl show "$UNIT" -p MainPID --value 2>/dev/null || echo '?')"
echo "SubState  : $(systemctl show "$UNIT" -p SubState --value 2>/dev/null || echo '?')"
echo "Result    : $(systemctl show "$UNIT" -p Result --value 2>/dev/null || echo '?')"

echo "── код / коммит ──"
if [ -f "$DIR/DEPLOYED_COMMIT" ]; then
  echo "DEPLOYED_COMMIT: $(cat "$DIR/DEPLOYED_COMMIT")"
fi
if [ -d "$DIR/.git" ]; then
  echo "git HEAD       : $(git -C "$DIR" rev-parse --short HEAD 2>/dev/null) ($(git -C "$DIR" rev-parse --abbrev-ref HEAD 2>/dev/null))"
fi

echo "── tenant / env (из окружения юнита) ──"
# systemctl show Environment + EnvironmentFiles не всегда раскрывает .env;
# берём ключевое из заявленного Environment юнита.
ENVS="$(systemctl show "$UNIT" -p Environment --value 2>/dev/null)"
for k in TENANT_STRICT TENANT_ID_EXPECTED TENANT_CONFIG DEFAULT_BRAND BILLING_ENABLED; do
  v="$(echo "$ENVS" | tr ' ' '\n' | grep "^$k=" | head -1)"
  echo "${v:-$k=(не задан в Environment юнита — возможно в EnvironmentFile)}"
done

echo "── пути state ──"
for p in pending.json billing/billing.db tenant.json; do
  f="$DIR/$p"
  [ -e "$f" ] && echo "$p : $(du -h "$f" 2>/dev/null | cut -f1) ($(stat -c %y "$f" 2>/dev/null | cut -d. -f1))" || echo "$p : НЕТ"
done

echo "── последние ошибки (journal, 200 строк → grep) ──"
journalctl -u "$UNIT" -n 200 --no-pager 2>/dev/null \
  | grep -iE "traceback|critical|error|conflict|unauthorized|tenant.*fatal" \
  | tail -15 || echo "(чисто или journal недоступен)"

echo "════════════════════════════════════════"
