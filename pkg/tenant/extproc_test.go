package tenant

import (
	"strings"
	"testing"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"

	"github.com/opendatahub-io/ai-gateway-controller/pkg/envelope"
)

func TestConfigureExternalModelExtProcProjectsReferencesAndTrustedHandoff(t *testing.T) {
	resources := []unstructured.Unstructured{
		{Object: map[string]any{
			"apiVersion": "v1", "kind": "ConfigMap", "metadata": map[string]any{"name": "payload-processing-external-model-plugins-redteam", "namespace": "tenant-a"},
			"data": map[string]any{"extproc.yaml": "old", "pre-extproc.yaml": "pre"},
		}},
		{Object: map[string]any{
			"apiVersion": "apps/v1", "kind": "Deployment", "metadata": map[string]any{"name": "payload-processing-external-model-redteam", "namespace": "tenant-a"},
			"spec": map[string]any{"template": map[string]any{"spec": map[string]any{
				"volumes":    []any{map[string]any{"name": "routing-overlay-volume"}},
				"containers": []any{map[string]any{"name": "payload-processing", "volumeMounts": []any{map[string]any{"name": "routing-overlay-volume"}}}},
			}}},
		}},
	}
	candidates := []envelope.Candidate{
		{
			Cluster:  "provider-provider-b",
			StableID: envelope.CandidateStableID("model-b", "provider-b"),
			Credential: &envelope.Credential{
				Strategy:  "bearer_token",
				SecretRef: envelope.SecretRef{Name: "b-secret", Namespace: "tenant-a", Key: "api-key"},
			},
		},
		{
			Cluster:  "provider-provider-a",
			StableID: envelope.CandidateStableID("model-a", "provider-a"),
			Credential: &envelope.Credential{
				Strategy:  "bearer_token",
				SecretRef: envelope.SecretRef{Name: "a-secret", Namespace: "tenant-a", Key: "api-key"},
			},
		},
	}
	if err := configureExternalModelExtProc(resources, "tenant-a", candidates, true); err != nil {
		t.Fatal(err)
	}
	data, found, err := unstructured.NestedStringMap(resources[0].Object, "data")
	if err != nil || !found {
		t.Fatalf("ConfigMap data missing: found=%v err=%v", found, err)
	}
	config, found := data["extproc.yaml"]
	if !found {
		t.Fatal("post-auth extproc.yaml is missing")
	}
	for _, want := range []string{
		"filter: intelligent_route",
		"filter: credential_inject",
		"provider_hop_clusters: [\"provider-provider-a\", \"provider-provider-b\"]",
		"max_body_bytes: 10485760",
		"api_translation_enabled: true",
		"max_body_bytes: 1048576",
		"strategy: bearer_token",
		"a-secret",
		"b-secret",
	} {
		if !strings.Contains(config, want) {
			t.Fatalf("post-auth config missing %q:\n%s", want, config)
		}
	}
	profiled := []string{
		"filter: intelligent_route",
		"filter: openai_chat_completions_to_anthropic_stream",
		"filter: openai_chat_completions_to_anthropic\n",
		"filter: credential_inject",
	}
	positions := make([]int, len(profiled))
	for i, want := range profiled {
		positions[i] = strings.Index(config, want)
		if positions[i] < 0 {
			t.Fatalf("post-auth config missing %q:\n%s", want, config)
		}
		if i > 0 && positions[i] <= positions[i-1] {
			t.Fatalf("provider selection, JSON, SSE, and credential filters are out of request/response order:\n%s", config)
		}
	}
	if strings.Contains(config, "secret-value") {
		t.Fatal("secret value appeared in post-auth configuration")
	}
	if strings.Contains(config, "allow_unbounded_body: true") {
		t.Fatal("dedicated ExternalModel ExtProc must enforce its configured body accumulation ceiling")
	}
	volumes, _, err := unstructured.NestedSlice(resources[1].Object, "spec", "template", "spec", "volumes")
	if err != nil {
		t.Fatal(err)
	}
	if len(volumes) != 2 {
		t.Fatalf("volumes = %d, want routing plus projected credentials", len(volumes))
	}
	annotations, _, err := unstructured.NestedStringMap(resources[1].Object, "spec", "template", "metadata", "annotations")
	if err != nil || annotations["ai-gateway-controller.opendatahub.io/extproc-config-sha256"] == "" {
		t.Fatalf("post-auth config hash missing: %#v (err=%v)", annotations, err)
	}
}

func TestExternalModelConfigOmitsCredentialFilterWithoutReferences(t *testing.T) {
	resources := []unstructured.Unstructured{
		{Object: map[string]any{
			"apiVersion": "v1", "kind": "ConfigMap", "metadata": map[string]any{"name": "payload-processing-external-model-plugins", "namespace": "tenant-a"},
			"data": map[string]any{"pre-extproc.yaml": "pre", "extproc.yaml": "old"},
		}},
		{Object: map[string]any{
			"apiVersion": "apps/v1", "kind": "Deployment", "metadata": map[string]any{"name": "payload-processing-external-model", "namespace": "tenant-a"},
			"spec": map[string]any{"template": map[string]any{"spec": map[string]any{
				"volumes": []any{}, "containers": []any{map[string]any{"name": "payload-processing", "volumeMounts": []any{}}},
			}}},
		}},
	}
	if err := configureExternalModelExtProc(resources, "tenant-a", nil, false); err != nil {
		t.Fatal(err)
	}
	data, _, err := unstructured.NestedStringMap(resources[0].Object, "data")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(data["extproc.yaml"], "credential_inject") {
		t.Fatalf("credential_inject must be omitted without credential references:\n%s", data["extproc.yaml"])
	}
	if !strings.Contains(data["extproc.yaml"], "api_translation_enabled: false") {
		t.Fatalf("ExternalModel ExtProc must fail closed when tenant translation permission is off:\n%s", data["extproc.yaml"])
	}
	hash, found, err := unstructured.NestedString(resources[1].Object, "spec", "template", "metadata", "annotations", "ai-gateway-controller.opendatahub.io/extproc-config-sha256")
	if err != nil || !found || hash == "" {
		t.Fatal("config hash is empty")
	}
}

func TestExtprocCredentialsRejectCrossNamespaceProvider(t *testing.T) {
	candidates := []envelope.Candidate{{
		StableID: envelope.CandidateStableID("model", "provider"),
		Credential: &envelope.Credential{
			Strategy:  "bearer_token",
			SecretRef: envelope.SecretRef{Name: "secret", Namespace: "other", Key: "api-key"},
		},
	}}
	if _, err := extprocCredentials("tenant-a", candidates); err == nil || !strings.Contains(err.Error(), "cross-namespace") {
		t.Fatalf("expected cross-namespace rejection, got %v", err)
	}
}

func TestExtprocCredentialsRejectsUnsupportedAuth(t *testing.T) {
	candidates := []envelope.Candidate{{
		StableID: envelope.CandidateStableID("model", "provider"),
		Credential: &envelope.Credential{
			Strategy:  "oauth2",
			SecretRef: envelope.SecretRef{Name: "secret", Namespace: "tenant-a", Key: "api-key"},
		},
	}}
	if _, err := extprocCredentials("tenant-a", candidates); err == nil || !strings.Contains(err.Error(), "unsupported ExtProc credential strategy") {
		t.Fatalf("expected unsupported-auth rejection, got %v", err)
	}
}

func TestSharedExtProcConfigDefaultOff(t *testing.T) {
	config := sharedExtProcConfig(false)
	if strings.Contains(config, "anthropic_messages_to_chat_completions") || strings.Contains(config, "path_rewrite") {
		t.Fatalf("default-off shared chain unexpectedly contains translation:\n%s", config)
	}
	if !strings.Contains(config, "filter: request_id") {
		t.Fatalf("default shared chain lost request_id:\n%s", config)
	}
}

func TestSharedExtProcConfigUsesRegisteredOrderedMessagesFilters(t *testing.T) {
	config := sharedExtProcConfig(true)
	ordered := []string{
		"filter: request_id",
		"filter: anthropic_messages_request",
		"filter: anthropic_messages_to_chat_completions",
		"filter: anthropic_messages_to_chat_completions_stream",
		"filter: headers",
		"filter: path_rewrite",
	}
	previous := -1
	for _, token := range ordered {
		position := strings.Index(config, token)
		if position <= previous {
			t.Fatalf("filter %q appears out of order (position=%d):\n%s", token, position, config)
		}
		previous = position
	}
	for _, want := range []string{
		"on_invalid: reject",
		"path: \"/v1/messages\"",
		"max_body_bytes: 1048576",
		"max_partial_event_bytes: 10485760",
		"content-type: \"text/event-stream\"",
		"pattern: \"^/v1/messages$\"",
		"replacement: \"/v1/chat/completions\"",
		"- x-api-key",
	} {
		if !strings.Contains(config, want) {
			t.Fatalf("translation chain missing %q:\n%s", want, config)
		}
	}
	for _, filter := range []string{"filter: headers", "filter: path_rewrite"} {
		start := strings.Index(config, filter)
		if start < 0 {
			t.Fatalf("translation chain missing %q:\n%s", filter, config)
		}
		end := strings.Index(config[start+len(filter):], "\n      - filter:")
		if end < 0 {
			end = len(config) - start - len(filter)
		}
		block := config[start : start+len(filter)+end]
		if !strings.Contains(block, "path: \"/v1/messages\"") {
			t.Fatalf("filter %q must be restricted to the exact Messages path:\n%s", filter, block)
		}
	}
}

func TestConfigureSharedExtProcDoesNotRewriteExternalModelResources(t *testing.T) {
	tenantID := "redteam"
	gatewayNamespace := "openshift-ingress"
	resources := []unstructured.Unstructured{
		{Object: map[string]any{
			"apiVersion": "v1", "kind": "ConfigMap",
			"metadata": map[string]any{"name": PayloadProcessingPluginsConfigMapForTenant(tenantID), "namespace": gatewayNamespace},
			"data":     map[string]any{"pre-extproc.yaml": "pre", "extproc.yaml": "old"},
		}},
		{Object: map[string]any{
			"apiVersion": "apps/v1", "kind": "Deployment",
			"metadata": map[string]any{"name": PayloadProcessingDeploymentName(tenantID), "namespace": gatewayNamespace},
			"spec": map[string]any{"template": map[string]any{
				"metadata": map[string]any{"annotations": map[string]any{}},
				"spec":     map[string]any{"containers": []any{map[string]any{"name": "payload-processing"}}},
			}},
		}},
		sharedEnvoyFilterFixture(tenantID, gatewayNamespace),
		{Object: map[string]any{
			"apiVersion": "v1", "kind": "ConfigMap",
			"metadata": map[string]any{"name": PayloadProcessingExternalModelPluginsConfigMapForTenant(tenantID), "namespace": "tenant-a"},
			"data":     map[string]any{"extproc.yaml": "external-model-chain"},
		}},
	}
	if err := configureSharedExtProc(resources, tenantID, gatewayNamespace, true); err != nil {
		t.Fatal(err)
	}
	data, _, err := unstructured.NestedStringMap(resources[0].Object, "data")
	if err != nil {
		t.Fatal(err)
	}
	if data["pre-extproc.yaml"] != "pre" || !strings.Contains(data["extproc.yaml"], "anthropic_messages_request") {
		t.Fatalf("unexpected shared ExtProc ConfigMap: %#v", data)
	}
	hash, found, err := unstructured.NestedString(resources[1].Object, "spec", "template", "metadata", "annotations", extprocConfigHashAnnotation)
	if err != nil || !found || hash == "" {
		t.Fatalf("shared config hash missing: found=%v err=%v hash=%q", found, err, hash)
	}
	externalData, _, err := unstructured.NestedStringMap(resources[3].Object, "data")
	if err != nil {
		t.Fatal(err)
	}
	if externalData["extproc.yaml"] != "external-model-chain" {
		t.Fatalf("shared config changed ExternalModel ExtProc: %#v", externalData)
	}
	assertSharedResponseMode(t, resources[2], "STREAMED")
}

func TestConfigureSharedExtProcKeepsDefaultResponseModeWhenTranslationIsOff(t *testing.T) {
	tenantID := "redteam"
	gatewayNamespace := "openshift-ingress"
	resources := []unstructured.Unstructured{
		{Object: map[string]any{
			"apiVersion": "v1", "kind": "ConfigMap",
			"metadata": map[string]any{"name": PayloadProcessingPluginsConfigMapForTenant(tenantID), "namespace": gatewayNamespace},
			"data":     map[string]any{"extproc.yaml": "old"},
		}},
		{Object: map[string]any{
			"apiVersion": "apps/v1", "kind": "Deployment",
			"metadata": map[string]any{"name": PayloadProcessingDeploymentName(tenantID), "namespace": gatewayNamespace},
			"spec": map[string]any{"template": map[string]any{
				"metadata": map[string]any{"annotations": map[string]any{}},
				"spec":     map[string]any{"containers": []any{map[string]any{"name": "payload-processing"}}},
			}},
		}},
		sharedEnvoyFilterFixture(tenantID, gatewayNamespace),
	}
	if err := configureSharedExtProc(resources, tenantID, gatewayNamespace, false); err != nil {
		t.Fatal(err)
	}
	assertSharedResponseMode(t, resources[2], "BUFFERED")
}

func sharedEnvoyFilterFixture(tenantID, namespace string) unstructured.Unstructured {
	patches := make([]any, 0, 8)
	for range 4 {
		for _, filter := range []struct {
			name string
			mode string
		}{
			{name: "envoy.filters.http.ext_proc.ipp", mode: "BUFFERED"},
			{name: "envoy.filters.http.ext_proc.ipp-pre", mode: "NONE"},
		} {
			patches = append(patches, map[string]any{
				"applyTo": "HTTP_FILTER",
				"patch": map[string]any{"value": map[string]any{
					"name":         filter.name,
					"typed_config": map[string]any{"processing_mode": map[string]any{"response_body_mode": filter.mode}},
				}},
			})
		}
	}
	return unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "networking.istio.io/v1alpha3", "kind": "EnvoyFilter",
		"metadata": map[string]any{"name": PayloadProcessingEnvoyFilterName(tenantID), "namespace": namespace},
		"spec":     map[string]any{"configPatches": patches},
	}}
}

func assertSharedResponseMode(t *testing.T, filter unstructured.Unstructured, want string) {
	t.Helper()
	patches, found, err := unstructured.NestedSlice(filter.Object, "spec", "configPatches")
	if err != nil || !found {
		t.Fatalf("shared EnvoyFilter configPatches: found=%t err=%v", found, err)
	}
	var ipp, ippPre int
	for _, raw := range patches {
		patch, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		name, _, _ := unstructured.NestedString(patch, "patch", "value", "name")
		mode, found, err := unstructured.NestedString(patch, "patch", "value", "typed_config", "processing_mode", "response_body_mode")
		if err != nil || !found {
			continue
		}
		switch name {
		case "envoy.filters.http.ext_proc.ipp":
			if mode != want {
				t.Errorf("shared ipp response_body_mode = %q, want %q", mode, want)
			}
			ipp++
		case "envoy.filters.http.ext_proc.ipp-pre":
			if mode != "NONE" {
				t.Errorf("shared ipp-pre response_body_mode = %q, want NONE", mode)
			}
			ippPre++
		}
	}
	if ipp != 4 || ippPre != 4 {
		t.Errorf("shared mode patches = ipp:%d ipp-pre:%d, want 4 each", ipp, ippPre)
	}
}
