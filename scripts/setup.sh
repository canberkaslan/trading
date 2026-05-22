#!/usr/bin/env bash
# Bootstrap script for local dev. Idempotent.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Checking prerequisites"
command -v uv >/dev/null 2>&1 || { echo "uv not found — install: https://docs.astral.sh/uv/"; exit 1; }
command -v pnpm >/dev/null 2>&1 || { echo "pnpm not found — install: https://pnpm.io/installation"; exit 1; }
command -v terraform >/dev/null 2>&1 || { echo "terraform not found — install: https://developer.hashicorp.com/terraform/install"; exit 1; }

echo "==> Setting up agent (Python 3.12)"
cd agent
uv venv
uv pip install -e ".[dev]"
[[ -f .env ]] || cp .env.example .env
cd ..

echo "==> Setting up mobile (Expo)"
cd mobile/app
pnpm install
cd ../..

echo "==> Setting up infra (Terraform init)"
cd infra/envs/dev
terraform init -backend=false  # offline init for local; real backend needs rootingo profile
cd ../../..

echo ""
echo "Setup complete. Next:"
echo "  - Fill in agent/.env with API keys"
echo "  - Run agent tests:    cd agent && uv run pytest"
echo "  - Start mobile:       cd mobile/app && pnpm start"
echo "  - Plan infra (rootingo):  cd infra/envs/dev && terraform init -backend-config=../../backend.hcl && terraform plan"
