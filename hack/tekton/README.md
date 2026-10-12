# Konflux group-test reference

The canonical pipeline normally lives in
[opendatahub-io/odh-konflux-central](https://github.com/opendatahub-io/odh-konflux-central):

`integration-tests/ai-gateway-controller/pr-group-testing-pipeline.yaml`

For the AIGC #123 diagnostic rerun, `.tekton/ai-gateway-controller-group-test.yaml`
resolves this copy from the PR source revision without setting an ExtProc image
override. The pipeline uses the ExtProc image from the generated group snapshot
when available, then falls back to `odh-stable`.

See `todo.md` for Konflux prerequisites and open items.
