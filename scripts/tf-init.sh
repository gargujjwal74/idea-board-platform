#!/usr/bin/env bash
# terraform init with the remote backend for a given cloud. The only per-cloud difference is
# the backend flavour (S3 vs GCS); everything else in the pipeline is cloud-blind.
# Usage: scripts/tf-init.sh <aws|gcp>   (env: NAME, STATE_BUCKET, REGION)
set -euo pipefail
cloud="${1:?usage: tf-init.sh <aws|gcp>}"
cd "$(dirname "$0")/../infra/envs/${cloud}"

case "$cloud" in
  aws)
    terraform init -input=false -reconfigure \
      -backend-config="bucket=${STATE_BUCKET}" \
      -backend-config="key=idea-board/${NAME}.tfstate" \
      -backend-config="region=${REGION}" \
      -backend-config="use_lockfile=true"
    ;;
  gcp)
    terraform init -input=false -reconfigure \
      -backend-config="bucket=${STATE_BUCKET}" \
      -backend-config="prefix=idea-board/${NAME}"
    ;;
  *) echo "unknown cloud: $cloud" >&2; exit 1 ;;
esac
