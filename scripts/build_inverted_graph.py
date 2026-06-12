#!/usr/bin/env python3
"""Reconstruit graph/inverted.json à partir des fichiers data/*.json.

Stdlib uniquement. Whitelist : seuls les repos `vidocq/*` sont acceptés.

Sous-modes :
  (par défaut)   Construit le graphe et écrit graph/inverted.json.
  --check        N'écrit rien. Sort 0 si zéro warning, 1 sinon. Pratique pour
                 valider en pre-commit ou en CI avant un push.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ALLOWED_OWNER_PREFIX = "vidocq/"
SCHEMA_VERSION = 1
LOG_FORMAT = "[%(levelname)s] %(message)s"
REQUIRED_DATA_KEYS = ("schema_version", "repo", "produces", "consumes")

logger = logging.getLogger("build_inverted_graph")


class WarningCounter(logging.Handler):
    """Handler logging qui se contente de compter les enregistrements >= WARNING."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.count += 1


def setup_logging(verbose: bool) -> WarningCounter:
    """Réinitialise le logger et y attache un handler de comptage.

    Idempotent : peut être appelée plusieurs fois (tests, mode --check).
    """
    level = logging.DEBUG if verbose else logging.INFO
    counter = WarningCounter()
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(logging.Formatter(LOG_FORMAT))
    stream.setLevel(level)
    logger.handlers.clear()
    logger.addHandler(stream)
    logger.addHandler(counter)
    logger.setLevel(level)
    logger.propagate = False
    return counter


def load_data_files(data_dir: Path) -> list[dict]:
    out: list[dict] = []
    for fp in sorted(glob.glob(str(data_dir / "*.json"))):
        path = Path(fp)
        if path.name == "schema.json":
            continue
        try:
            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except json.JSONDecodeError as exc:
            logger.warning("JSON invalide (%s) : %s", path.name, exc)
            continue
        missing = [k for k in REQUIRED_DATA_KEYS if k not in payload]
        if missing:
            logger.warning(
                "%s : champ(s) manquant(s) selon le schéma : %s",
                path.name, ", ".join(missing),
            )
            continue
        repo = payload.get("repo")
        # Case-insensitive: the owner was migrated vidocq/* -> Vidocq/* on Codeberg,
        # so accept either casing rather than silently dropping every repo.
        if not isinstance(repo, str) or not repo.lower().startswith(ALLOWED_OWNER_PREFIX):
            logger.warning(
                "%s : repo hors whitelist `vidocq/*` ignoré (%r)",
                path.name, repo,
            )
            continue
        out.append(payload)
    return out


def build(payloads: list[dict]) -> dict:
    artifacts: dict[str, dict] = {}

    # Pass 1 : producteurs.
    for p in payloads:
        repo = p["repo"]
        produces = p.get("produces", [])
        logger.info("%s : %d artefact(s) produit(s).", repo, len(produces))
        for prod in produces:
            key = f"{prod['groupId']}:{prod['artifactId']}"
            entry = artifacts.setdefault(
                key, {"produced_by": None, "consumed_by": []}
            )
            if entry["produced_by"] and entry["produced_by"] != repo:
                logger.warning(
                    "Collision de producteurs sur %s : %s ET %s — conserve %s.",
                    key, entry["produced_by"], repo, entry["produced_by"],
                )
                continue
            entry["produced_by"] = repo

    # Pass 2 : consommateurs.
    for p in payloads:
        repo = p["repo"]
        for cons in p.get("consumes", []):
            key = f"{cons['groupId']}:{cons['artifactId']}"
            entry = artifacts.setdefault(
                key, {"produced_by": None, "consumed_by": []}
            )
            scope = cons.get("scope", "compile")
            entry["consumed_by"].append({"repo": repo, "scope": scope})

    # Tri stable + déduplication des consumed_by.
    for entry in artifacts.values():
        seen = set()
        deduped = []
        for c in entry["consumed_by"]:
            t = (c["repo"], c["scope"])
            if t in seen:
                continue
            seen.add(t)
            deduped.append(c)
        deduped.sort(key=lambda c: (c["repo"], c["scope"]))
        entry["consumed_by"] = deduped

    detect_orphans(artifacts)
    detect_cycles(artifacts)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "artifacts": artifacts,
    }


def detect_orphans(artifacts: dict[str, dict]) -> None:
    """Artefact consommé mais sans producteur indexé."""
    for key in sorted(artifacts):
        entry = artifacts[key]
        if entry.get("produced_by") is None and entry.get("consumed_by"):
            consumers = sorted({c["repo"] for c in entry["consumed_by"]})
            logger.warning(
                "Artefact orphelin %s : consommé par %s mais aucun producteur indexé.",
                key, ", ".join(consumers),
            )


def detect_cycles(artifacts: dict[str, dict]) -> None:
    """DFS sur le graphe `repo -> repos consommateurs` ; signale les cycles."""
    repo_consumers: dict[str, set[str]] = {}
    for entry in artifacts.values():
        producer = entry.get("produced_by")
        if not producer:
            continue
        for c in entry["consumed_by"]:
            repo_consumers.setdefault(producer, set()).add(c["repo"])

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    for r, downs in repo_consumers.items():
        color.setdefault(r, WHITE)
        for d in downs:
            color.setdefault(d, WHITE)

    def visit(node: str, path: list[str]) -> None:
        color[node] = GRAY
        for nxt in repo_consumers.get(node, ()):
            if color.get(nxt, WHITE) == GRAY:
                logger.warning("Cycle détecté : %s", " -> ".join(path + [nxt]))
            elif color.get(nxt, WHITE) == WHITE:
                visit(nxt, path + [nxt])
        color[node] = BLACK

    for n in list(color):
        if color[n] == WHITE:
            visit(n, [n])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Reconstruit graph/inverted.json depuis data/*.json."
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="Active les logs DEBUG (verbose I/O et progression).",
    )
    p.add_argument(
        "--check", action="store_true",
        help="Mode dry-run : n'écrit rien, sort 1 si au moins un warning.",
    )
    return p.parse_args(argv)


def run_check(data_dir: Path, verbose: bool = False) -> tuple[int, int]:
    """Exécute le pipeline en mémoire (sans écrire) et retourne (exit_code, warnings).

    Pratique pour les tests.
    """
    counter = setup_logging(verbose)
    payloads = load_data_files(data_dir)
    logger.info("%d fichier(s) data/*.json lus.", len(payloads))
    graph = build(payloads)
    logger.info("Graphe inversé : %d artefact(s) au total.", len(graph["artifacts"]))
    return (1 if counter.count > 0 else 0), counter.count


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    counter = setup_logging(args.verbose)

    here = Path(__file__).resolve().parent.parent
    data_dir = here / "data"
    out_path = here / "graph" / "inverted.json"

    payloads = load_data_files(data_dir)
    logger.info("%d fichier(s) data/*.json lus.", len(payloads))

    graph = build(payloads)
    logger.info("Graphe inversé : %d artefact(s) au total.", len(graph["artifacts"]))

    if args.check:
        if counter.count > 0:
            logger.info("--check : %d warning(s) — sortie 1.", counter.count)
            return 1
        logger.info("--check : aucun warning — sortie 0.")
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(graph, fh, indent=2, sort_keys=True)
        fh.write("\n")
    logger.info("Écrit : %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
