#!/usr/bin/env bash
set -euo pipefail
release="${1:?Pass the Git commit SHA}"
[[ "$release" =~ ^[0-9a-f]{40}$ ]] || exit 2
base=/opt/gamepulse
directory="$base/releases/$release"
exec 9>"$base/deploy.lock"
flock -w 600 9
test -s "$base/.env"
previous=$(readlink -f "$base/current" || true)
sudo -n docker load -i "$directory/image.tar.gz"
export GAMEPULSE_IMAGE="gamepulse:$release"
compose=(sudo -n --preserve-env=GAMEPULSE_IMAGE docker compose --project-name gamepulse --env-file "$base/.env" -f "$directory/compose.production.yaml")
if "${compose[@]}" up -d --wait --wait-timeout 180 && curl --fail --silent --retry 5 --retry-delay 3 http://127.0.0.1:18000/health; then
  ln -sfn "$directory" "$base/current"
  rm -f "$directory/image.tar.gz"
  echo "Deployed $release"
else
  echo 'Deployment failed; restoring previous release' >&2
  if [[ -n "$previous" && -f "$previous/compose.production.yaml" ]]; then
    export GAMEPULSE_IMAGE="gamepulse:$(basename "$previous")"
    sudo -n --preserve-env=GAMEPULSE_IMAGE docker compose --project-name gamepulse --env-file "$base/.env" -f "$previous/compose.production.yaml" up -d --wait --wait-timeout 180
  fi
  exit 1
fi
