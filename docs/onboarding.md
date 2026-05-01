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
- `previous_tag_command`: release-script command used as the changelog base tag.

After editing `fleet.yml`:

```bash
python -m aio_fleet export-app-manifest --repo <repo> --write
python -m aio_fleet validate-repo --repo <repo> --repo-path ../<repo>
python -m aio_fleet cleanup-repo --repo <repo> --verify
python -m aio_fleet control-check --repo <repo> --sha <commit-sha> --event pull_request --dry-run
python -m aio_fleet doctor
```
