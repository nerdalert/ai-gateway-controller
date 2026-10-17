#!/usr/bin/env bash
set -Eeuo pipefail

RUN_ROOT=${AIGC_KIND_E2E_RUN_ROOT:?AIGC_KIND_E2E_RUN_ROOT must identify this workflow run}
TOOL_DIR="$RUN_ROOT/tools"
BIN_DIR="$TOOL_DIR/bin"
ARTIFACT_DIR="$RUN_ROOT/artifact"
mkdir -p "$BIN_DIR" "$TOOL_DIR/downloads" "$RUN_ROOT/deps" "$ARTIFACT_DIR"

readonly KIND_VERSION=v0.30.0
readonly KUBECTL_VERSION=v1.31.12
readonly HELM_VERSION=v3.18.6
readonly KUSTOMIZE_VERSION=v5.7.1
readonly YQ_VERSION=v4.45.1
readonly ISTIO_VERSION=1.27.3
readonly KIND_NODE_IMAGE=kindest/node:v1.31.12@sha256:9b0369c99755c4201e15618015138240182efd9cf2ba21351498d2e0176e8169

require() {
  command -v "$1" >/dev/null 2>&1 || { echo "missing required command: $1" >&2; exit 1; }
}

download_checked() {
  local url=$1 expected_sha=$2 destination=$3
  curl --fail --location --retry 4 --retry-delay 2 --silent --show-error "$url" -o "$destination"
  printf '%s  %s\n' "$expected_sha" "$destination" | sha256sum --check --status || {
    echo "checksum mismatch for $url" >&2
    exit 1
  }
}

for tool in curl git sha256sum tar; do require "$tool"; done
df -h / >"$ARTIFACT_DIR/disk-before-runner-setup.txt"
# Kind, Docker image builds, and imported node images need substantially more
# room than the standard hosted image leaves after its developer SDKs. This
# runs only on the disposable Actions VM and removes only unused preinstalled
# SDK/tool directories; it does not prune Docker images or cluster resources.
sudo python3 - <<'PY'
from pathlib import Path
import shutil

for raw_path in (
    "/usr/local/lib/android",
    "/usr/share/dotnet",
    "/opt/ghc",
    "/usr/local/share/powershell",
    "/usr/local/share/chromium",
    "/opt/hostedtoolcache/CodeQL",
):
    path = Path(raw_path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        print(f"removed_unused_runner_tool={path}")
PY
sudo apt-get clean
sudo apt-get update
sudo apt-get install --yes --no-install-recommends gettext-base jq ripgrep

download_checked \
  "https://github.com/kubernetes-sigs/kind/releases/download/${KIND_VERSION}/kind-linux-amd64" \
  517ab7fc89ddeed5fa65abf71530d90648d9638ef0c4cde22c2c11f8097b8889 \
  "$TOOL_DIR/downloads/kind"
install -m 0755 "$TOOL_DIR/downloads/kind" "$BIN_DIR/kind"

download_checked \
  "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
  cf609add577be9c898c52027e800a008331d6b2a202ecc61413e847f7a12ccd0 \
  "$TOOL_DIR/downloads/kubectl"
install -m 0755 "$TOOL_DIR/downloads/kubectl" "$BIN_DIR/kubectl"

download_checked \
  "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" \
  3f43c0aa57243852dd542493a0f54f1396c0bc8ec7296bbb2c01e802010819ce \
  "$TOOL_DIR/downloads/helm.tar.gz"
mkdir -p "$TOOL_DIR/helm"
tar -xzf "$TOOL_DIR/downloads/helm.tar.gz" -C "$TOOL_DIR/helm"
install -m 0755 "$TOOL_DIR/helm/linux-amd64/helm" "$BIN_DIR/helm"

download_checked \
  "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize/${KUSTOMIZE_VERSION}/kustomize_${KUSTOMIZE_VERSION}_linux_amd64.tar.gz" \
  ea375e7372f9aa029129d4b2d16c66b7750b7f1213c4f66f910d981c895818d8 \
  "$TOOL_DIR/downloads/kustomize.tar.gz"
mkdir -p "$TOOL_DIR/kustomize"
tar -xzf "$TOOL_DIR/downloads/kustomize.tar.gz" -C "$TOOL_DIR/kustomize"
install -m 0755 "$TOOL_DIR/kustomize/kustomize" "$BIN_DIR/kustomize"

download_checked \
  "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_amd64" \
  654d2943ca1d3be2024089eb4f270f4070f491a0610481d128509b2834870049 \
  "$TOOL_DIR/downloads/yq"
install -m 0755 "$TOOL_DIR/downloads/yq" "$BIN_DIR/yq"

download_checked \
  "https://github.com/istio/istio/releases/download/${ISTIO_VERSION}/istioctl-${ISTIO_VERSION}-linux-amd64.tar.gz" \
  7faad5c06c11859ae292c6f2684bb60142dc649e1abfb5abff83e2e2353d29e6 \
  "$TOOL_DIR/downloads/istioctl.tar.gz"
ISTIO_DIR="$TOOL_DIR/istio-${ISTIO_VERSION}"
mkdir -p "$ISTIO_DIR/bin"
tar -xzf "$TOOL_DIR/downloads/istioctl.tar.gz" -C "$ISTIO_DIR/bin" istioctl
chmod 0755 "$ISTIO_DIR/bin/istioctl"
sha256sum \
  "$TOOL_DIR/downloads/kind" \
  "$TOOL_DIR/downloads/kubectl" \
  "$TOOL_DIR/downloads/helm.tar.gz" \
  "$TOOL_DIR/downloads/kustomize.tar.gz" \
  "$TOOL_DIR/downloads/yq" \
  "$TOOL_DIR/downloads/istioctl.tar.gz" \
  >"$ARTIFACT_DIR/tool-checksums.txt"

export PATH="$BIN_DIR:$PATH"
echo "$BIN_DIR" >>"$GITHUB_PATH"
{
  echo "AIGC_KIND_E2E_RUN_ROOT=$RUN_ROOT"
  echo "LOCAL_ENV_CLUSTER=${LOCAL_ENV_CLUSTER:?LOCAL_ENV_CLUSTER must be set by the workflow}"
  echo "LOCAL_ENV_EVIDENCE_ROOT=$ARTIFACT_DIR/evidence"
  echo "LOCAL_ENV_DEPS_DIR=$RUN_ROOT/deps"
  echo "KIND_NODE_IMAGE=$KIND_NODE_IMAGE"
  echo "ISTIOCTL=$ISTIO_DIR/bin/istioctl"
  echo "AI_CONTROLLER_IMAGE=ai-gateway-controller:gha-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
  echo "EXTPROC_IMAGE=praxis-extproc:gha-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
  echo "MAAS_API_IMAGE=maas-api:gha-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
  echo "MAAS_CONTROLLER_IMAGE=maas-controller:gha-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
  echo "PROVIDER_RECORDER_IMAGE=provider-recorder:gha-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
  echo "KATAN_KIND_IMAGE=llm-katan:gha-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
} >>"$GITHUB_ENV"

helm repo add jetstack https://charts.jetstack.io --force-update
helm repo add kuadrant https://kuadrant.io/helm-charts/ --force-update
helm repo update

capture_tool_versions() {
  {
    echo "runner_os=$(uname -srm)"
    echo "go=$(go version)"
    echo "docker=$(docker version --format '{{.Client.Version}} / {{.Server.Version}}')"
    echo "git=$(git --version)"
    echo "jq=$(jq --version)"
    echo "ripgrep=$(rg --version | head -1)"
    echo "envsubst=$(envsubst --version | head -1)"
    echo "openssl=$(openssl version)"
    echo "kind=$(kind version)"
    echo "kubectl=$(kubectl version --client=true -o json | jq -r '.clientVersion.gitVersion')"
    echo "helm=$(helm version --short)"
    echo "kustomize=$(kustomize version)"
    echo "yq=$(yq --version)"
    echo "istioctl=$("$ISTIO_DIR/bin/istioctl" version --remote=false | head -1)"
    echo "gateway_api=v1.5.1"
    echo "cert_manager_chart=v1.15.3"
    echo "kuadrant_chart=1.3.1"
    echo "katan_image_index=ghcr.io/nerdalert/llm-katan@sha256:11379a1ec2fd69dc121eada6c544eb423a7c074414507dc1d474f4abba9df75a"
    echo "katan_image_linux_amd64=ghcr.io/nerdalert/llm-katan@sha256:a8bf18109e2db641ef4a63efe65f69d4d6554f1128a174053de89a8b81b4284d"
    echo "kind_node_image=$KIND_NODE_IMAGE"
  } >"$ARTIFACT_DIR/tool-versions.txt"
  docker info --format 'docker_arch={{.Architecture}} docker_cpus={{.NCPU}} docker_memory_bytes={{.MemTotal}}' >"$ARTIFACT_DIR/docker-host.txt"
  df -h "$RUN_ROOT" >"$ARTIFACT_DIR/disk-after-tool-install.txt"
  docker info >/dev/null
}
capture_tool_versions

clone_source() {
  local name=$1 url=$2 sha=$3 destination="$RUN_ROOT/deps/$1"
  git init -q "$destination"
  git -C "$destination" remote add origin "$url"
  git -C "$destination" fetch --depth=1 origin "$sha"
  git -C "$destination" -c advice.detachedHead=false checkout --detach FETCH_HEAD
  local actual
  actual=$(git -C "$destination" rev-parse HEAD)
  [[ "$actual" == "$sha" ]] || { echo "$name checkout mismatch: expected=$sha actual=$actual" >&2; exit 1; }
  [[ -z "$(git -C "$destination" status --porcelain)" ]] || { echo "$name checkout is unexpectedly dirty" >&2; exit 1; }
}

readonly MAAS_SHA=d2c4fc133d5c3dd8cd95138982a5de9951db0af0
readonly KSERVE_SHA=5d738906d22e003166d09ae28733366420e30d8d
readonly EXTPROC_SHA=06db0b9e5fece6b523332a5990346abd8148c5e9
clone_source models-as-a-service https://github.com/opendatahub-io/models-as-a-service.git "$MAAS_SHA"
clone_source kserve https://github.com/kserve/kserve.git "$KSERVE_SHA"
clone_source praxis-extproc https://github.com/opendatahub-io/praxis-extproc.git "$EXTPROC_SHA"

{
  echo "{"
  echo '  "sources": {'
  printf '    "models-as-a-service": {"url":"https://github.com/opendatahub-io/models-as-a-service.git","sha":"%s"},\n' "$MAAS_SHA"
  printf '    "kserve": {"url":"https://github.com/kserve/kserve.git","sha":"%s"},\n' "$KSERVE_SHA"
  printf '    "praxis-extproc": {"url":"https://github.com/opendatahub-io/praxis-extproc.git","sha":"%s"}\n' "$EXTPROC_SHA"
  echo '  }'
  echo '}'
} >"$ARTIFACT_DIR/dependency-source.json"
{
  echo "MAAS_CONTROLLER_REPO=$RUN_ROOT/deps/models-as-a-service"
  echo "KSERVE_REPO=$RUN_ROOT/deps/kserve"
  echo "PRAXIS_EXTPROC_REPO=$RUN_ROOT/deps/praxis-extproc"
} >>"$GITHUB_ENV"

echo "Disk available after tool and source setup:"
df -h "$RUN_ROOT"
available_kib=$(df -Pk "$RUN_ROOT" | awk 'NR == 2 {print $4}')
if [[ ! "$available_kib" =~ ^[0-9]+$ ]] || (( available_kib < 8 * 1024 * 1024 )); then
  echo "less than 8 GiB of free disk remains for Kind and image builds" >&2
  exit 1
fi
