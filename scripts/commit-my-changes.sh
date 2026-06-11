#!/usr/bin/env bash
# Stage WebUI/Telegram work — excludes secrets and local decoy edits.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! git config user.email >/dev/null 2>&1; then
  echo "Set identity first: ./scripts/setup-my-github.sh \"Name\" email@example.com github_user"
  exit 1
fi

# Drop local template overrides (keep repo defaults)
git restore decoy_data/ 2>/dev/null || true

git add \
  .gitignore \
  requirements.txt \
  run-web.py \
  webui/ \
  app/client/ciam.py \
  app/client/encrypt.py \
  app/menus/bookmark.py \
  app/menus/hot.py \
  app/service/auth.py \
  app/service/bookmark.py \
  app/service/decoy.py \
  scripts/

# Safety: never commit secrets
git reset HEAD webui_data/ monitor.log .env refresh-tokens.json 2>/dev/null || true

if git diff --cached --quiet; then
  echo "Nothing to commit (already clean?)."
  exit 0
fi

git status --short
echo ""
git commit -m "$(cat <<'EOF'
feat(webui): add FastAPI Web UI and Telegram bot integration

- Multi-user webui with session auth, per-user data dirs, and cwd isolation
- Telegram bot: kuota/saldo display, purchase flows, bookmarks, decoy payments
- Security: safer error responses, bookmark resolution by option name
- Quota display fix (no false unlimited on byte-sized DATA quotas)
EOF
)"

echo ""
echo "Commit OK. Push to YOUR fork:"
echo "  git push -u origin main"
echo "(Create fork on GitHub first if you haven't.)"