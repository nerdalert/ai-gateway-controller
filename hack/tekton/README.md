# Konflux group-test reference

The canonical pipeline normally lives in
[opendatahub-io/odh-konflux-central](https://github.com/opendatahub-io/odh-konflux-central):

`integration-tests/ai-gateway-controller/pr-group-testing-pipeline.yaml`

For the AIGC #123 image qualification, `.tekton/ai-gateway-controller-group-test.yaml`
resolves this copy from the PR source revision and passes the pinned ExtProc image
digest as `praxis-extproc-image`. This keeps the test override in the AIGC PR and
does not change the shared pipeline.

See `todo.md` for Konflux prerequisites and open items.
