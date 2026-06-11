#!/usr/bin/env bash
# Setup SSH + git identity for pushing to YOUR fork (not purplemashu's repo).
set -euo pipefail
cd "$(dirname "$0")/.."

KEY_PATH="${HOME}/.ssh/id_ed25519_me_cli_sunset"
KEY_PUB="${KEY_PATH}.pub"

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 \"Your Name\" your.email@example.com [github_username]"
  echo ""
  echo "Example:"
  echo "  $0 \"Arifian\" arifian@users.noreply.github.com mygithubuser"
  exit 1
fi

GIT_NAME="$1"
GIT_EMAIL="$2"
GITHUB_USER="${3:-}"

if [[ ! -f "$KEY_PATH" ]]; then
  echo "Generating SSH key: $KEY_PATH"
  mkdir -p "${HOME}/.ssh"
  chmod 700 "${HOME}/.ssh"
  ssh-keygen -t ed25519 -C "${GIT_EMAIL}" -f "$KEY_PATH" -N ""
else
  echo "SSH key already exists: $KEY_PATH"
fi

# ssh config snippet
CONFIG="${HOME}/.ssh/config"
if [[ -f "$CONFIG" ]] && grep -q "Host github-me-cli" "$CONFIG" 2>/dev/null; then
  echo "ssh config host github-me-cli already present."
else
  cat >> "$CONFIG" <<EOF

# me-cli-sunset → your GitHub account
Host github-me-cli
  HostName github.com
  User git
  IdentityFile ${KEY_PATH}
  IdentitiesOnly yes
EOF
  chmod 600 "$CONFIG" 2>/dev/null || true
  echo "Added Host github-me-cli to ~/.ssh/config"
fi

git config user.name "$GIT_NAME"
git config user.email "$GIT_EMAIL"

echo ""
echo "=== Public key (paste at GitHub → Settings → SSH keys) ==="
cat "$KEY_PUB"
echo "=== end public key ==="
echo ""

if [[ -n "$GITHUB_USER" ]]; then
  UPSTREAM_URL="$(git remote get-url origin 2>/dev/null || true)"
  if [[ "$UPSTREAM_URL" == *purplemashu* ]]; then
    if git remote | grep -q '^upstream$'; then
      echo "remote upstream already exists"
    else
      git remote rename origin upstream
      echo "Renamed origin → upstream ($UPSTREAM_URL)"
    fi
  fi
  if git remote | grep -q '^origin$'; then
    git remote set-url origin "git@github-me-cli:${GITHUB_USER}/me-cli-sunset.git"
  else
    git remote add origin "git@github-me-cli:${GITHUB_USER}/me-cli-sunset.git"
  fi
  echo "origin → git@github-me-cli:${GITHUB_USER}/me-cli-sunset.git"
  echo "Fork dulu di GitHub: https://github.com/purplemashu/me-cli-sunset → Fork"
fi

echo ""
echo "Done. Test: ssh -T git@github-me-cli"
echo "Commit: ./scripts/commit-my-changes.sh"