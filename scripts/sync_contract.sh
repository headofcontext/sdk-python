#!/usr/bin/env bash
# Copy the OpenAPI contract from the core repository (sibling checkout by default).
set -euo pipefail
CORE="${1:-$(dirname "$0")/../../headofcontext}"
cp "$CORE/docs/openapi.json" "$(dirname "$0")/../contract/openapi.json"
echo "contract synced from $CORE"
