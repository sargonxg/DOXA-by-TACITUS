#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
mode="${1:---help}"
config="${DOXA_WRANGLER_CONFIG:-infra/cloudflare/wrangler.jsonc}"
case "$config" in
  /*) config_path="$config" ;;
  *) config_path="$repo_root/$config" ;;
esac
if [[ "$mode" != "--check" && "$mode" != "--apply" ]]; then
  cat <<'TEXT'
DOXA Cloudflare deployment
  scripts/deploy-cloudflare.sh --check   Build/test locally; no cloud resources created.
  scripts/deploy-cloudflare.sh --apply   Apply remote schema and deploy the configured app.

Before --apply, provision D1/R2/Queues explicitly, replace config placeholders,
configure Cloudflare Access and secrets, and review docs/deployment.md and current prices.
Set DOXA_WRANGLER_CONFIG to use a separate Wrangler configuration file.
TEXT
  exit 0
fi
npm --prefix frontend ci --no-audit --no-fund
npm --prefix frontend run build
npm --prefix infra/cloudflare ci --no-audit --no-fund
npm --prefix infra/cloudflare run typecheck
npm --prefix infra/cloudflare test
if [[ "$mode" == "--check" ]]; then
  printf '%s\n' 'Local checks passed. No cloud resources or deployments were changed.'
  exit 0
fi
if python -c 'import pathlib,sys; t=pathlib.Path(sys.argv[1]).read_text(); sys.exit(0 if "REPLACE_" in t or "00000000-0000-0000-0000-000000000000" in t else 1)' "$config_path"; then
  printf '%s\n' 'Replace all deployment placeholders before --apply.' >&2
  exit 1
fi
command -v docker >/dev/null
docker info >/dev/null
# These commands mutate your configured Cloudflare account and may incur charges.
# Keep a new D1 workspace free of application tables until Store.init can
# distinguish fresh bootstrap from a real upgrade requiring a backup receipt.
npm --prefix infra/cloudflare exec -- wrangler d1 migrations apply DB --remote --config "$config_path"
npm --prefix infra/cloudflare exec -- wrangler deploy --config "$config_path"
