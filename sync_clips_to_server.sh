#!/usr/bin/env bash
# Sync staged clips to nox-maksim server with proper layout:
#   D:/clips-to-upload/<category>/{photos,videos}/  →  server:
#     /home/maksim-bot/maksim-bot/broll-library/
#       ├── photos/maksim/<category>/   (.jpg)
#       └── clips/maksim/<category>/    (.mov, .mp4)
#
# Glamping variants (glamping, glamping_holiday, glamping_evening) merge
# into a single `glamping/` on server — sub-flavor is preserved in the
# JSON sidecars' tags field.

set -euo pipefail

LOCAL_ROOT="D:/AI/maksim-bot/clips-to-upload"
SSH_KEY="C:/Users/Dell/.ssh/id_ed25519"
SSH_TARGET="root@89.167.89.133"
SERVER_ROOT="/home/maksim-bot/maksim-bot/broll-library"

# Categories: (local_dir, server_subdir)
declare -a CATEGORIES=(
    "sup:sup"
    "karting:karting"
    "glamping:glamping"
    "glamping_holiday:glamping"
    "glamping_evening:glamping"
    "personal:personal"
)

# Ensure server dirs exist
echo "Creating server directories..."
ssh -i "$SSH_KEY" "$SSH_TARGET" "
mkdir -p \
    $SERVER_ROOT/photos/maksim/{sup,karting,glamping,personal} \
    $SERVER_ROOT/clips/maksim/{sup,karting,glamping,personal}
"

for entry in "${CATEGORIES[@]}"; do
    local_cat="${entry%%:*}"
    server_cat="${entry##*:}"

    local_photos="$LOCAL_ROOT/$local_cat/photos/"
    local_videos="$LOCAL_ROOT/$local_cat/videos/"
    server_photos="$SERVER_ROOT/photos/maksim/$server_cat/"
    server_videos="$SERVER_ROOT/clips/maksim/$server_cat/"

    if [[ -d "$local_photos" ]] && [[ "$(ls -A "$local_photos" 2>/dev/null)" ]]; then
        echo "==> Photos: $local_cat → server:$server_cat"
        rsync -av --progress \
              -e "ssh -i $SSH_KEY" \
              "$local_photos" "$SSH_TARGET:$server_photos" \
              | tail -3
    fi

    if [[ -d "$local_videos" ]] && [[ "$(ls -A "$local_videos" 2>/dev/null)" ]]; then
        echo "==> Videos: $local_cat → server:$server_cat"
        rsync -av --progress \
              -e "ssh -i $SSH_KEY" \
              "$local_videos" "$SSH_TARGET:$server_videos" \
              | tail -3
    fi
done

# Fix ownership on server (uploaded as root; bot runs as maksim-bot)
echo "Fixing ownership..."
ssh -i "$SSH_KEY" "$SSH_TARGET" "
chown -R maksim-bot:maksim-bot $SERVER_ROOT/photos/maksim/ $SERVER_ROOT/clips/maksim/
"

# Final inventory
echo ""
echo "============================================================"
echo "Server inventory:"
ssh -i "$SSH_KEY" "$SSH_TARGET" "
for cat in sup karting glamping personal; do
    p=\$(find $SERVER_ROOT/photos/maksim/\$cat -maxdepth 1 -type f 2>/dev/null | wc -l)
    v=\$(find $SERVER_ROOT/clips/maksim/\$cat -maxdepth 1 -type f 2>/dev/null | wc -l)
    pj=\$(find $SERVER_ROOT/photos/maksim/\$cat -maxdepth 1 -name '*.json' 2>/dev/null | wc -l)
    vj=\$(find $SERVER_ROOT/clips/maksim/\$cat -maxdepth 1 -name '*.json' 2>/dev/null | wc -l)
    echo \"  \$cat: photos=\$p (json:\$pj)  clips=\$v (json:\$vj)\"
done
du -sh $SERVER_ROOT/photos/maksim $SERVER_ROOT/clips/maksim 2>/dev/null
"
