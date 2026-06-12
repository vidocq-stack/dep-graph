# `graph/` — graphe de dépendances inversé

Ce dossier contient le résultat agrégé du graphe de dépendances Vidocq.

## `inverted.json`

Reconstruit automatiquement à chaque push touchant `data/**` (workflow
`.forgejo/workflows/build-graph.yml`). Ne pas éditer à la main.

Format :

```json
{
  "schema_version": 1,
  "generated_at": "2026-05-10T12:35:00Z",
  "artifacts": {
    "io.vidocq.vauban:vauban-core": {
      "produced_by": "vidocq/vauban",
      "consumed_by": [
        {"repo": "vidocq/cassini",  "scope": "compile"},
        {"repo": "vidocq/foy",      "scope": "compile"},
        {"repo": "vidocq/mansart",  "scope": "test"}
      ]
    }
  }
}
```

- Clé : `groupId:artifactId` (sans version).
- `produced_by` : repo Forgejo qui publie l'artefact.
- `consumed_by` : liste des repos consommateurs avec le `scope` Maven (`compile`,
  `runtime`, `provided`, `test`, `import`, `build` pour les plugins).
- Les listes sont triées par `(repo, scope)` pour produire des diffs stables.

## Règles

- Whitelist : seuls les fichiers `data/<repo>.json` dont le champ `repo` commence
  par `vidocq/` sont indexés (cf. `scripts/build_inverted_graph.py`).
- Si un même artefact est déclaré comme produit par deux repos, le premier rencontré
  gagne et un avertissement est imprimé sur stderr du builder (cas pathologique).
- Les cycles repo→repo sont détectés et signalés sur stderr, sans faire échouer
  la construction.

## Comment l'utiliser

Côté producteur, pour découvrir les consommateurs impactés par une PR :

```bash
python3 scripts/resolve_impact.py \
  --graph graph/inverted.json \
  --changed io.vidocq.vauban:vauban-core io.vidocq.vauban:vauban-api \
  --format matrix
```

Sortie :

```json
{"include": [{"repo": "vidocq/cassini"}, {"repo": "vidocq/foy"}, {"repo": "vidocq/mansart"}, {"repo": "vidocq/vidocq"}]}
```

Ajouter `--ordered` pour trier la matrice en ordre topologique (producteur avant
consommateur), requis par le rebuild séquentiel de `ci/build-impacted` :

```json
{"include": [{"repo": "vidocq/cassini"}, {"repo": "vidocq/foy"}, {"repo": "vidocq/mansart"}, {"repo": "Vidocq/vidocq"}]}
```

Cette liste est consommée par l'action `ci/build-impacted` (job `pr-validate`)
qui reclone et rebuild chaque consommateur dans l'ordre, contre les artefacts PR
du `~/.m2` local du runner.
