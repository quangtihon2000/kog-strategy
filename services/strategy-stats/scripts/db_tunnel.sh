#!/usr/bin/env bash
# SSH tunnel to strategy-stats Postgres on the prod VPS.
# Run from your local machine.
#
# Postgres is docker-network-only (compose does NOT publish 5432 to the host),
# so we resolve the container IP via `docker inspect` and forward through it.
# Local port defaults to 5434 to avoid clashing with portfolio-engine's tunnel
# (which conventionally uses 5433).
#
# Usage:
#   ./scripts/db_tunnel.sh
#   PROD_HOST=deploy@1.2.3.4 ./scripts/db_tunnel.sh
#   PG_LOCAL=5500 ./scripts/db_tunnel.sh
#
# Connect from local (password from VPS .env: POSTGRES_PASSWORD):
#   psql "postgresql://stats_user:<password>@127.0.0.1:5434/strategy_stats"
#   DBeaver / TablePlus -> host=127.0.0.1 port=5434 user=stats_user db=strategy_stats

set -euo pipefail

PROD_HOST="${PROD_HOST:-pae-prod}"
PG_CONTAINER="${PG_CONTAINER:-strategy-stats-postgres-1}"
PG_LOCAL="${PG_LOCAL:-5434}"

echo "==> Resolving ${PG_CONTAINER} IP on ${PROD_HOST}..."
PG_IP=$(ssh "${PROD_HOST}" \
  "docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' ${PG_CONTAINER}" \
  | awk '{print $1}')

if [[ -z "${PG_IP}" ]]; then
  echo "ERROR: could not resolve ${PG_CONTAINER} IP — is the container running?" >&2
  exit 1
fi

echo "==> Tunnel: 127.0.0.1:${PG_LOCAL} -> ${PG_IP}:5432 (via ${PROD_HOST})"
echo "==> Ctrl-C to close."
exec ssh -N -L "${PG_LOCAL}:${PG_IP}:5432" "${PROD_HOST}"
