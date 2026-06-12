# Vidocq dependency graph (public, read-only mirror of the build graph)

This repository holds the **cross-repo dependency graph** of the Vidocq ecosystem.
It is **public on purpose**: the PR-validation CI (`Vidocq/ci/build-impacted`) reads
it on every pull request — **including PRs opened from external forks**, which do not
receive organisation secrets. Because every artefact here is non-sensitive build
metadata, no token is required to read it, so fork PRs run their full downstream
validation with **zero secrets**.

## Layout

```
data/Vidocq_<repo>.json   one file per producer repo: the io.vidocq.* artefacts it
                          produces and consumes (source of truth)
data/schema.json          JSON schema for the data files
graph/inverted.json       reverse graph (artefact -> downstream consumer repos),
                          rebuilt from data/ by scripts/build_inverted_graph.py
scripts/                  graph tooling shared by the CI actions:
  extract_deps.sh           extract a repo's io.vidocq.* deps  (used by update-dep-graph)
  update_data_file.sh       write data/<repo>.json + push      (used by update-dep-graph)
  build_inverted_graph.py   rebuild graph/inverted.json        (used by build-graph.yml)
  resolve_impact.py         resolve impacted consumers         (used by build-impacted)
  validate_data.py          data integrity checks
```

## Who writes here

- `Vidocq/ci/update-dep-graph` (runs on a producer's `push: main` touching `pom.xml`)
  rewrites `data/<repo>.json` and pushes — with the org bot token, on **internal** pushes only.
- `.forgejo/workflows/build-graph.yml` (this repo) rebuilds `graph/inverted.json`
  whenever `data/**` changes.

## Who reads here

- `Vidocq/ci/build-impacted` (runs on every producer PR) fetches `graph/inverted.json`,
  `scripts/resolve_impact.py` and `data/<repo>.json` — **anonymously**, no token needed.

> Project-management content (tasks, plans) stays in the private `Vidocq/GestionProjet`.
> Only the build graph lives here.
