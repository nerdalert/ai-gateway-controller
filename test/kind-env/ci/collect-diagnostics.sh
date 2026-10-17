#!/usr/bin/env bash
set -Eeuo pipefail

CLUSTER=${LOCAL_ENV_CLUSTER:?LOCAL_ENV_CLUSTER must identify the Kind cluster for this run}
RUN_ROOT=${AIGC_KIND_E2E_RUN_ROOT:?AIGC_KIND_E2E_RUN_ROOT must identify this workflow run}
OUT="$RUN_ROOT/artifact/diagnostics"
mkdir -p "$OUT"

capture() {
  local file=$1
  shift
  set +e
  "$@" >"$OUT/$file" 2>&1
  local rc=$?
  set -e
  printf 'exit_code=%s command=' "$rc" >"$OUT/${file}.status"
  printf '%q ' "$@" >>"$OUT/${file}.status"
  printf '\n' >>"$OUT/${file}.status"
  return 0
}

capture kind-clusters.txt kind get clusters
capture docker-containers.txt docker ps -a --no-trunc
capture docker-networks.txt docker network ls --no-trunc
capture docker-images.txt docker image ls --digests --no-trunc

CONTEXT="kind-$CLUSTER"
if ! kubectl --context "$CONTEXT" cluster-info >"$OUT/cluster-info.txt" 2>&1; then
  echo "cluster_available=false" >"$OUT/cluster-availability.txt"
  exit 0
fi
echo "cluster_available=true" >"$OUT/cluster-availability.txt"

capture kubectl-version.yaml kubectl --context "$CONTEXT" version -o yaml
capture nodes.txt kubectl --context "$CONTEXT" get nodes -o wide
capture pods.txt kubectl --context "$CONTEXT" get pods --all-namespaces -o wide
capture workloads.txt kubectl --context "$CONTEXT" get deployments,statefulsets,daemonsets,services,endpoints --all-namespaces -o wide
capture events.txt kubectl --context "$CONTEXT" get events --all-namespaces --sort-by=.lastTimestamp
capture crds.txt kubectl --context "$CONTEXT" get crds

kubectl --context "$CONTEXT" get pods --all-namespaces -o json 2>"$OUT/pod-image-identities.err" \
  | jq '[.items[] | {namespace:.metadata.namespace,name:.metadata.name,uid:.metadata.uid,phase:.status.phase,ready:([.status.conditions[]? | select(.type=="Ready" and .status=="True")] | length == 1),containers:([.status.containerStatuses[]? | {name,image,imageID,ready,restartCount}])}]' \
  >"$OUT/pod-image-identities.json" || true

for resource in gateways httproutes externalmodels externalproviders maastenantconfigs aitenants; do
  capture "${resource}.yaml" kubectl --context "$CONTEXT" get "$resource" --all-namespaces -o yaml
done

for deployment in \
  maas-system/maas-controller \
  maas-system/maas-api \
  maas-system/maas-api-tenant-b \
  maas-system/maas-api-transition \
  maas-system/maas-default-gateway-istio \
  maas-system/maas-tenant-b-gateway-istio \
  maas-system/maas-transition-gateway-istio \
  opendatahub/ai-gateway-controller \
  models-as-a-service/payload-processing-external-model \
  maas-system/payload-pre-processing; do
  namespace=${deployment%/*}
  name=${deployment#*/}
  capture "logs-${namespace}-${name}.txt" kubectl --context "$CONTEXT" --namespace "$namespace" logs "deployment/$name" --all-containers=true --tail=400 --prefix=true
done

echo "diagnostics_complete=true" >"$OUT/result.txt"
