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
  # Kind creates a shared Docker bridge named exactly "kind". Remove it only
  # when this run demonstrably created it: it was absent before qualification,
  # there were no Kind clusters before or after, and no containers remain
  # attached. Preserve any pre-existing or in-use network.
  if [[ -f "$RUN_ROOT/artifact/inventory-before/kind-clusters.txt" \
    && -f "$RUN_ROOT/artifact/inventory-before/docker-networks.txt" ]]; then
    cp "$OUT/docker-networks.txt" "$OUT/docker-networks-before-owned-cleanup.txt"
    no_clusters_before=true
    no_clusters_after=true
    if awk 'NF && $0 != "No kind clusters found." { found=1 } END { exit found ? 0 : 1 }' \
      "$RUN_ROOT/artifact/inventory-before/kind-clusters.txt"; then
      no_clusters_before=false
    fi
    if awk 'NF && $0 != "No kind clusters found." { found=1 } END { exit found ? 0 : 1 }' "$OUT/kind-clusters.txt"; then
      no_clusters_after=false
    fi
    kind_network_before=false
    if awk 'NR > 1 && $2 == "kind" { found=1 } END { exit found ? 0 : 1 }' \
      "$RUN_ROOT/artifact/inventory-before/docker-networks.txt"; then
      kind_network_before=true
    fi
    if [[ "$no_clusters_before" == true && "$no_clusters_after" == true && "$kind_network_before" == false ]]; then
      if docker network inspect kind >/dev/null 2>&1; then
        endpoint_count=$(docker network inspect --format '{{len .Containers}}' kind)
        if [[ "$endpoint_count" == 0 ]]; then
          if docker network rm kind >"$OUT/kind-network-removal.txt" 2>&1; then
            echo 'kind_network_removed=true reason=created_by_this_run_and_no_endpoints' >"$OUT/kind-network-cleanup.txt"
          else
            echo 'kind_network_removed=false reason=network_rm_failed' >"$OUT/kind-network-cleanup.txt"
            cat "$OUT/kind-network-removal.txt" >>"$OUT/kind-network-cleanup.txt"
            exit 1
          fi
        else
          printf 'kind_network_removed=false reason=network_has_%s_endpoints\n' "$endpoint_count" \
            >"$OUT/kind-network-cleanup.txt"
          exit 1
        fi
      else
        echo 'kind_network_removed=false reason=already_absent_after_cluster_deletion' >"$OUT/kind-network-cleanup.txt"
      fi
    else
      echo 'kind_network_removed=false reason=preexisting_kind_network_or_cluster' >"$OUT/kind-network-cleanup.txt"
    fi
    docker network ls --no-trunc >"$OUT/docker-networks.txt" || {
      echo 'post-cleanup Docker network inventory failed' >"$OUT/inventory-error.txt"
      exit 1
    }
  else
    echo 'run-start inventory unavailable; cannot establish ownership of a Docker network' >"$OUT/kind-network-cleanup.txt"
    exit 1
  fi

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
