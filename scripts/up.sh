#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
docker compose up -d
echo "waiting for postgres"
i=0
while [ "$i" -lt 60 ]; do
  if docker compose exec -T postgres pg_isready -U postgres >/dev/null 2>&1; then
    break
  fi
  i=$((i + 1))
  sleep 1
done
exec cargo run --manifest-path coordinator/Cargo.toml
