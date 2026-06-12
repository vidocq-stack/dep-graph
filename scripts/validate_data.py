#!/usr/bin/env python3
"""Valide tous les data/*.json contre le schéma JSON minimal embarqué.

Pas de dépendance externe : on ré-implémente la validation des contraintes
(types, required, enum, pattern) en stdlib uniquement.
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

GROUP_PATTERN = re.compile(r"^io\.vidocq(\.[A-Za-z0-9_-]+)*$")
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
SCOPES = {"compile", "runtime", "provided", "test", "system", "import", "build"}


def validate_payload(path: Path) -> list[str]:
    errs: list[str] = []
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"JSON invalide : {exc}"]

    if d.get("schema_version") != 1:
        errs.append("schema_version != 1")

    repo = d.get("repo")
    if not isinstance(repo, str) or not REPO_PATTERN.match(repo):
        errs.append(f"repo invalide : {repo!r}")
    elif not repo.lower().startswith("vidocq/"):
        errs.append(f"repo hors whitelist `Vidocq/*` (org Codeberg) : {repo}")

    for field in ("branch", "commit_sha"):
        v = d.get(field)
        if not isinstance(v, str) or not v:
            errs.append(f"{field} manquant ou vide")

    updated = d.get("updated_at")
    if not isinstance(updated, str) or not DATE_PATTERN.match(updated):
        errs.append(f"updated_at invalide : {updated!r}")

    for arr_field in ("produces", "consumes"):
        arr = d.get(arr_field)
        if not isinstance(arr, list):
            errs.append(f"{arr_field} doit être une liste")
            continue
        for i, item in enumerate(arr):
            if not isinstance(item, dict):
                errs.append(f"{arr_field}[{i}] : doit être un objet")
                continue
            g = item.get("groupId")
            a = item.get("artifactId")
            if not isinstance(g, str) or not GROUP_PATTERN.match(g):
                errs.append(f"{arr_field}[{i}].groupId invalide : {g!r}")
            if not isinstance(a, str) or not a:
                errs.append(f"{arr_field}[{i}].artifactId manquant")
            if "version" not in item:
                errs.append(f"{arr_field}[{i}].version manquant")
            if arr_field == "consumes":
                scope = item.get("scope")
                if scope not in SCOPES:
                    errs.append(f"consumes[{i}].scope invalide : {scope!r}")
    return errs


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    data_dir = here / "data"
    files = sorted(p for p in data_dir.glob("*.json") if p.name != "schema.json")
    if not files:
        print("validate_data: aucun fichier data/*.json à valider.", file=sys.stderr)
        return 0

    failed = False
    for fp in files:
        errs = validate_payload(fp)
        if errs:
            failed = True
            print(f"FAIL  {fp.name}")
            for e in errs:
                print(f"  - {e}")
        else:
            print(f"OK    {fp.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
