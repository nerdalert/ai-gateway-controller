#!/usr/bin/env bash
set -Eeuo pipefail

MODE=${1:?usage: inventory.sh before|after}
case "$MODE" in before|after) ;; *) echo "invalid inventory mode: $MODE" >&2; exit 2 ;; esac
CLUSTER=${LOCAL_ENV_CLUSTER:?LOCAL_ENV_CLUSTER must identify the Kind cluster for this run}
RUN_ROOT=${AIGC_KIND_E2E_RUN_ROOT:?AIGC_KIND_E2E_RUN_ROOT must identify this workflow run}
OUT="$RUN_ROOT/artifact/inventory-$MODE"
mkdir -p "$OUT"

kind_rc=0
docker_containers_rc=0
docker_networks_rc=0
docker_images_rc=0
docker_disk_rc=0
kind get clusters >"$OUT/kind-clusters.txt" 2>&1 || kind_rc=$?
docker ps -a --no-trunc >"$OUT/docker-containers.txt" 2>&1 || docker_containers_rc=$?
docker network ls --no-trunc >"$OUT/docker-networks.txt" 2>&1 || docker_networks_rc=$?
docker image ls --digests --no-trunc >"$OUT/docker-images.txt" 2>&1 || docker_images_rc=$?
docker system df -v >"$OUT/docker-disk-usage.txt" 2>&1 || docker_disk_rc=$?
df -h "$RUN_ROOT" >"$OUT/host-disk.txt" 2>&1 || true
printf 'kind=%s\ndocker_containers=%s\ndocker_networks=%s\ndocker_images=%s\ndocker_disk=%s\n' \
  "$kind_rc" "$docker_containers_rc" "$docker_networks_rc" "$docker_images_rc" "$docker_disk_rc" \
  >"$OUT/inventory-exit-statuses.txt"
if (( kind_rc != 0 || docker_containers_rc != 0 || docker_networks_rc != 0 || docker_images_rc != 0 || docker_disk_rc != 0 )); then
  echo "inventory commands failed; ownership or cleanup cannot be proven" >"$OUT/inventory-error.txt"
  exit 1
fi

if [[ "$MODE" == before ]]; then
  if rg -qx "$CLUSTER" "$OUT/kind-clusters.txt" \
    || awk -v prefix="${CLUSTER}-" 'NR > 1 && index($NF, prefix) == 1 { found=1 } END { exit found ? 0 : 1 }' "$OUT/docker-containers.txt"; then
    echo "run-owned cluster name already exists before qualification; refusing to reuse or delete it" >"$OUT/ownership.txt"
    exit 1
  fi
  printf 'cluster_name=%s\nclaim_created=true\n' "$CLUSTER" >"$OUT/ownership.txt"
  : >"$RUN_ROOT/cluster-claim"
  echo 'claimed=true' >>"$GITHUB_OUTPUT"
else
  printf 'cluster_name=%s\n' "$CLUSTER" >"$OUT/cleanup-check.txt"
  if rg -qx "$CLUSTER" "$OUT/kind-clusters.txt"; then
    echo 'cluster_removed=false' >>"$OUT/cleanup-check.txt"
    exit 1
  fi
  if awk -v prefix="${CLUSTER}-" 'NR > 1 && index($NF, prefix) == 1 { found=1 } END { exit found ? 0 : 1 }' "$OUT/docker-containers.txt"; then
    echo 'run_owned_docker_container_remaining=true' >>"$OUT/cleanup-check.txt"
    exit 1
  fi
  echo 'cluster_removed=true' >>"$OUT/cleanup-check.txt"
  echo 'run_owned_docker_containers_removed=true' >>"$OUT/cleanup-check.txt"
  if [[ -f "$RUN_ROOT/artifact/inventory-before/docker-networks.txt" ]]; then
    if diff -u "$RUN_ROOT/artifact/inventory-before/docker-networks.txt" "$OUT/docker-networks.txt" >"$OUT/docker-network-diff.txt"; then
      echo 'docker_network_inventory_changed=false' >>"$OUT/cleanup-check.txt"
    else
      echo 'docker_network_inventory_changed=true; see docker-network-diff.txt; shared networks were not manually removed' >>"$OUT/cleanup-check.txt"
    fi
  fi
  echo 'post_cleanup=PASS' >"$OUT/result.txt"
fi
