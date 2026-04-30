# AGENTS.md

This repository is the fleet control plane for the JSONbored Unraid AIO repos.

## Intent

- Keep shared CI/release/drift policy out of individual app repos.
- Keep app repos focused on app runtime, Docker, XML, docs, and tests.
- Treat `fleet.yml` as the canonical manifest for repo metadata and exceptions.

## Rules

- Prefer reusable workflows and generated thin callers over copied workflow YAML.
- Do not move app-specific runtime logic into this repo unless it is genuinely shared.
- Keep `unraid-aio-template` as the repo bootstrap template.
- Keep `awesome-unraid` as the downstream Community Apps catalog.
- Any publish-related change must preserve integration-test gates.

