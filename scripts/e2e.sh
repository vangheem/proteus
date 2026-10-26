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

if [ ! -x coordinator/target/debug/proteus ]; then
  cargo build --manifest-path coordinator/Cargo.toml
fi

mkdir -p var
if [ -f var/coordinator.pid ]; then
  kill "$(cat var/coordinator.pid)" 2>/dev/null || true
  i=0
  while [ "$i" -lt 20 ]; do
    if ! curl -sf http://127.0.0.1:8080/__proteus/health >/dev/null 2>&1; then
      break
    fi
    i=$((i + 1))
    sleep 1
  done
fi
if curl -sf http://127.0.0.1:8080/__proteus/health >/dev/null 2>&1; then
  echo "coordinator already listening"
else
  echo "starting coordinator"
  cargo build --manifest-path coordinator/Cargo.toml
  PROTEUS_ABILITY_EXECUTOR=echo ./coordinator/target/debug/proteus > var/coordinator.log 2>&1 &
  echo $! > var/coordinator.pid
  i=0
  while [ "$i" -lt 240 ]; do
    if curl -sf http://127.0.0.1:8080/__proteus/health >/dev/null 2>&1; then
      break
    fi
    i=$((i + 1))
    sleep 2
  done
fi

if ! curl -sf http://127.0.0.1:8080/__proteus/health >/dev/null 2>&1; then
  echo "coordinator did not become healthy" >&2
  tail -n 80 var/coordinator.log >&2 || true
  exit 1
fi

python3 tests/e2e/run.py
