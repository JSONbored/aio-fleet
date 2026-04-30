# Releases

`unraid-aio-template` uses normal semver releases.

## Version format

- patch release for compatible template fixes: `vX.Y.Z`
- minor release for additive template features: `vX.Y.0`
- major release for intentional breaking changes: `vX.0.0`

## Release flow

1. Trigger **Prepare Release / Template** from `main`.
2. Review and merge the generated release PR.
3. Wait for CI on the release target commit to finish green.
4. Trigger **Publish Release / Template** from `main`.
