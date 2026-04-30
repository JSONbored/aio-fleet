# Unraid Support Thread Template

This template repo keeps a generic support-thread outline for future AIO apps. Derived app repos should customize the placeholders before Community Apps submission.

## Copy-paste template

```md
# Support: {{APP_NAME}} ({{SHORT_DESCRIPTOR}} for Unraid)

## What this is

{{APP_NAME}} is {{ONE_SENTENCE_APP_DESCRIPTION}}.

This AIO package exists to make {{UPSTREAM_APP_NAME}} easier to install and maintain on Unraid without forcing users to manually translate a multi-container setup, wire extra dependencies, or guess at first-boot defaults.

## Tradeoffs

- Updates to {{UPSTREAM_APP_NAME}} may lag while the AIO packaging is validated and rebuilt.
- Some advanced upstream configuration paths may not be exposed in the default Unraid template.
- This packaging may behave differently from the official multi-container deployment guide when the AIO wrapper chooses simpler defaults.

## Quick install notes

- Image: `{{IMAGE_NAME}}`
- Default WebUI: `{{WEBUI_URL_OR_NOTE}}`
- Main appdata path: `{{APPDATA_PATHS}}`
- Required setup fields: `{{REQUIRED_FIELDS}}`

## Support scope

This thread covers the JSONbored Unraid AIO packaging for {{APP_NAME}}.
```
