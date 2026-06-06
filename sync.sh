#!/bin/bash
# Sync content-bot files between local and server
# Usage: bash sync.sh [up|down|both]

# Env-overridable so the same script works for any tenant deploy.
# Defaults match the current nox-maksim production layout.
SERVER="${MAKSIM_BOT_SSH:-root@89.167.89.133}"
REMOTE_DIR="${REMOTE_BOT_ROOT:-/home/maksim-bot/maksim-bot}"
LOCAL_DIR="$(dirname "$0")"
SSH_KEY="${MAKSIM_BOT_SSH_KEY:-$HOME/.ssh/id_rsa}"
SSH_PORT="${MAKSIM_BOT_SSH_PORT:-22}"
SSH_OPTS="-o StrictHostKeyChecking=no -i $SSH_KEY"
SCP_OPTS="$SSH_OPTS -P $SSH_PORT"
SSH_CMD_OPTS="$SSH_OPTS -p $SSH_PORT"

MODE="${1:-both}"

echo "🔄 Syncing content-bot..."

if [ "$MODE" = "up" ] || [ "$MODE" = "both" ]; then
    echo "⬆️  Uploading to server..."
    # Bot code
    scp $SCP_OPTS "$LOCAL_DIR/bot.py" "$SERVER:$REMOTE_DIR/bot.py"
    scp $SCP_OPTS "$LOCAL_DIR/crosspost.py" "$SERVER:$REMOTE_DIR/crosspost.py"
    scp $SCP_OPTS "$LOCAL_DIR/instagram_dm.py" "$SERVER:$REMOTE_DIR/instagram_dm.py"
    scp $SCP_OPTS "$LOCAL_DIR/youtube_auth.py" "$SERVER:$REMOTE_DIR/youtube_auth.py"
    scp $SCP_OPTS "$LOCAL_DIR/instagram_auth.py" "$SERVER:$REMOTE_DIR/instagram_auth.py"
    scp $SCP_OPTS "$LOCAL_DIR/requirements.txt" "$SERVER:$REMOTE_DIR/requirements.txt"
    scp $SCP_OPTS "$LOCAL_DIR/script_prompt.txt" "$SERVER:$REMOTE_DIR/script_prompt.txt"
    scp $SCP_OPTS "$LOCAL_DIR/.env" "$SERVER:$REMOTE_DIR/.env"
    # Avatars
    scp $SCP_OPTS "$LOCAL_DIR/assets/avatars/"* "$SERVER:$REMOTE_DIR/assets/avatars/"
    echo "✅ Upload done"
fi

if [ "$MODE" = "down" ] || [ "$MODE" = "both" ]; then
    echo "⬇️  Downloading from server..."
    # Projects folder
    mkdir -p "$LOCAL_DIR/projects"
    scp $SCP_OPTS -r "$SERVER:$REMOTE_DIR/projects/"* "$LOCAL_DIR/projects/" 2>/dev/null
    # Voice files
    scp $SCP_OPTS -r "$SERVER:$REMOTE_DIR/assets/voices/"* "$LOCAL_DIR/assets/voices/" 2>/dev/null
    # Critical server-only files (backup)
    mkdir -p "$LOCAL_DIR/server_backup"
    scp $SCP_OPTS "$SERVER:$REMOTE_DIR/.env" "$LOCAL_DIR/server_backup/.env" 2>/dev/null
    scp $SCP_OPTS "$SERVER:$REMOTE_DIR/instagram_token.json" "$LOCAL_DIR/server_backup/instagram_token.json" 2>/dev/null
    scp $SCP_OPTS "$SERVER:$REMOTE_DIR/youtube_token.json" "$LOCAL_DIR/server_backup/youtube_token.json" 2>/dev/null
    scp $SCP_OPTS "$SERVER:$REMOTE_DIR/dm_keywords.json" "$LOCAL_DIR/server_backup/dm_keywords.json" 2>/dev/null
    scp $SCP_OPTS "$SERVER:$REMOTE_DIR/dm_log.json" "$LOCAL_DIR/server_backup/dm_log.json" 2>/dev/null
    echo "✅ Download done"
fi

if [ "$MODE" = "restart" ] || [ "$MODE" = "both" ]; then
    echo "🔄 Restarting bot on server..."
    ssh $SSH_CMD_OPTS $SERVER "ps aux | grep 'python3 bot.py' | grep -v grep | awk '{print \$2}' | xargs kill 2>/dev/null; sleep 2; cd $REMOTE_DIR && screen -dmS bot python3 bot.py"
    echo "✅ Bot restarted"
fi

echo "🎉 Sync complete!"
