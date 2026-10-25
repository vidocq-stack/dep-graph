# Vidocq dependency graph

This repository holds the **cross-repository dependency graph** of the Vidocq ecosystem: which
repository produces which `io.vidocq.*` artifact, and which repositories consume it. It is the
source of truth for the pull-request downstream check of every producer.

It is **public on purpose**: the CI reads it on every pull request, including pull requests from
forks, which receive no organisation secrets. Everything here is non-sensitive build metadata, so
reading needs no token, and a fork's pull request runs its downstream check with zero secrets.

## Layout

```
data/Vidocq_<repo>.json   one file per repository: the io.vidocq.* artifacts it produces and
                          the io.vidocq.* artifacts it consumes (written by update-dep-graph)
data/schema.json          JSON schema of the data files
graph/inverted.json       the reverse graph (artifact -> consumer repositories), rebuilt from
                          data/ by scripts/build_inverted_graph.py; format in graph/README.md
scripts/
  extract_deps.sh           extracts a repository's io.vidocq.* artifacts and dependencies
  update_data_file.sh       writes data/<owner>_<repo>.json, rebuilds graph/inverted.json,
                            commits both together and pushes
  build_inverted_graph.py   rebuilds graph/inverted.json (--check: dry run, exit 1 on a warning)
  resolve_impact.py         the transitive consumers of a set of artifacts
  validate_data.py          checks every data/*.json against the schema's constraints
  tests/                    unittest suites (python3 -m unittest discover -s scripts/tests)
```

## What `extract_deps.sh` records

`extract_deps.sh <project-root> <owner/repo>` parses the `pom.xml` files of the repository's
reactor directly (Python standard library only, no Maven, so it works before anything is
installed). It follows `<modules>` (and `<subprojects>`), and for the root project, every module
and **every profile** of each, it records the `io.vidocq.*` coordinates found in:

- `<dependencies>` (scope as declared, `compile` by default) and `<dependencyManagement>`;
- `<build>` and `<reporting>` plugins, in `<plugins>` **and** `<pluginManagement>`: the plugin
  itself, its own `<dependencies>`, and the `<annotationProcessorPaths>` of its configuration,
  top level or in an execution (all recorded with scope `build`).

Profiles count because a TCK runner or a quality build declared in a profile (`-Ptck`) still
depends on the producer. Artifacts the repository produces itself are left out of `consumes`.

## Who writes here

- **`Vidocq/ci/update-dep-graph`**, from each repository's `update-dep-graph.yml` (on a `main`
  push touching a `pom.xml`, or by hand): it downloads `extract_deps.sh` and
  `update_data_file.sh` from this repository, extracts the data, then `update_data_file.sh`
  writes `data/<owner>_<repo>.json`, rebuilds `graph/inverted.json` **in the same commit**, and
  pushes with the organisation bot token. On a push conflict it starts again from the new branch
  head (the graph depends on every data file, so it is rebuilt rather than rebased), up to five
  times.
- These commits carry the CI-skip marker, so that each repository's update starts no workflow
  here. That is why the graph is rebuilt by `update_data_file.sh` itself: when it was left to
  `build-graph.yml`, it stayed frozen while `data/` moved, and the downstream check rebuilt too
  few consumers (Vidocq/vidocq#285).
- `.forgejo/workflows/build-graph.yml` still rebuilds `graph/inverted.json` on a commit without
  the marker that touches `data/**` or `scripts/build_inverted_graph.py` (a manual edit, a change
  of the builder).

## Who reads here

All of them read anonymously; a token, when there is one, only lifts rate limits.

- `Vidocq/ci/resolve-impacted` (every producer pull request) and `Vidocq/ci/build-impacted` (the
  nightly sequential chain) fetch `graph/inverted.json`, `scripts/resolve_impact.py` and the
  producer's `data/<repo>.json`. Before resolving the consumers they check that the graph's
  `generated_at` is not older than the producer data's `updated_at`: a stale graph may miss
  consumers, and fails the job.
- `Vidocq/ci/build-one-consumer` reads `graph/inverted.json` only with `graph-pruning: true` (off
  by default).
- `Vidocq/ci/refresh-downstream-release-yml` reads it to find the consumers to refresh after a
  release.

## Working locally

```bash
bash scripts/extract_deps.sh /path/to/cassini Vidocq/cassini     # preview a data file
python3 scripts/validate_data.py                                 # check every data/*.json
python3 scripts/build_inverted_graph.py --check                  # dry run, exit 1 on a warning
python3 scripts/resolve_impact.py --graph graph/inverted.json \
  --changed io.vidocq.vauban:vauban-core --format matrix --ordered
python3 -m unittest discover -s scripts/tests
```

Do not commit a hand-made data file: run the repository's `update-dep-graph` workflow by hand
(`workflow_dispatch`) instead.

How a new repository registers, and how the CI uses the graph, is described in
[`Vidocq/GestionProjet`](https://codefloe.com/Vidocq/GestionProjet) (`README.md`,
`HOWTO_ADD_NEW_PROJECT.md`). The copy of the graph that `Vidocq/GestionProjet` still holds is
obsolete: this repository is the only live one.
