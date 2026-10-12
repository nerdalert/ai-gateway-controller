# Konflux group-test reference

The shared pipeline normally lives in
[opendatahub-io/odh-konflux-central](https://github.com/opendatahub-io/odh-konflux-central):

`integration-tests/ai-gateway-controller/pr-group-testing-pipeline.yaml`

For AIGC #123 qualification, `.tekton/ai-gateway-controller-group-test.yaml`
resolves the pipeline copy in this PR and passes the pinned ExtProc image digest
as `praxis-extproc-image`. This keeps the test override in the AIGC PR and does
not change the shared pipeline.

See `todo.md` for Konflux prerequisites and open items.
