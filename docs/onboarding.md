# Repository Onboarding

Use `unraid-aio-template` to create the new app repo first. Then add it to `fleet.yml`.

Required manifest fields:

- `path`: local checkout path used by operator commands.
- `app_slug`: stable fleet slug.
- `workflow_name`: user-facing GitHub Actions workflow name.
- `image_name`: image path without registry, for example `jsonbored/example-aio`.
- `docker_cache_scope`: GitHub Actions cache scope for the main image.
- `pytest_image_tag`: local image tag used by integration tests.
- `publish_profile`: one of `template`, `upstream-aio-track`, `changelog-version`, `dify`, or `signoz-suite`.
- `release_name`: user-facing release workflow name.
- `upstream_name` and `image_description`: OCI metadata labels.
- `xml_paths`: XML/template paths watched by CI.
- `catalog_assets`: source-to-target copy rules for `awesome-unraid` sync.

Optional fields:

- `generated_template`: marks repos whose XML is generated from source data.
- `generator_check_command`: command run before XML validation.
- `checkout_submodules`: required for repos like `mem0-aio`.
- `extra_publish_paths`: paths that should trigger image publishing.
- `extended_integration`: manual extended integration test input and pytest args.
- `components`: component-aware publish lanes, currently used by `signoz-aio`.
- `upstream_components`: matrix values for component-aware upstream checks.
- `upstream_commit_paths`: files committed by an upstream monitor PR.
- `upstream_monitor`: version/digest sources used by central upstream PR automation.
- `previous_tag_command`: release-script command used as the changelog base tag.

## Playbook

1. Create the repo from `unraid-aio-template`.
2. Keep only app-specific source/runtime/template/docs/tests in the app repo.
3. Add the repo to `fleet.yml`, including Docker Hub image name, XML assets,
   release profile, and upstream monitor config.
4. Export `.aio-fleet.yml` into the app repo.
5. Validate the app repo through central policy and cleanup checks.
6. Run a central control-check dry-run against the app commit.
7. Run upstream-monitor and registry dry-runs.
8. Sync catalog assets into `awesome-unraid` with a PR when the source repo is ready.
9. Render the support-thread draft and complete CA-facing metadata review.
10. Prove `aio-fleet / required` appears on a real app PR before branch protection
    depends on it.

Acceptance commands:

```bash
python -m aio_fleet export-app-manifest --repo <repo> --write
python -m aio_fleet validate-repo --repo <repo> --repo-path ../<repo>
python -m aio_fleet cleanup-repo --repo <repo> --verify
python -m aio_fleet cleanup-repo --repo <repo> --fix --verify
python -m aio_fleet control-check --repo <repo> --sha <commit-sha> --event pull_request --dry-run
python -m aio_fleet upstream monitor --repo <repo> --dry-run
python -m aio_fleet registry verify --repo <repo> --sha <commit-sha> --dry-run --verbose
python -m aio_fleet sync-catalog --repo <repo> --catalog-path ../awesome-unraid --dry-run
python -m aio_fleet support-thread render --repo <repo>
python -m aio_fleet doctor
```

## Multi-Image Repos

Use `signoz-aio` as the reference when a repo publishes more than one image.
Declare each image under `components`, give each component its own cache scope,
Dockerfile/context, upstream version key, release suffix, and integration test
args, then add matching `upstream_monitor` entries. `aio-fleet` will publish and
verify each component separately while keeping one app repo and one required
fleet check.
