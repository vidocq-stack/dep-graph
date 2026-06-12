#!/usr/bin/env bash
# extract_deps.sh — produit le JSON `data/<owner>_<repo>.json` pour un projet Maven.
#
# Usage : extract_deps.sh <project_root> <owner/repo>
# Sortie : JSON sur stdout, conforme au schéma data/schema.json.
#
# Stratégie : parsing XML direct des pom.xml (stdlib Python uniquement, pas de mvn requis).
# Pourquoi pas `mvn dependency:list` ? Parce que le bootstrap doit fonctionner avant
# que les artefacts internes soient installés dans le repo local.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <project_root> <owner/repo>" >&2
  exit 2
fi

PROJECT_ROOT="$1"
REPO_FULL_NAME="$2"

if [[ ! -d "$PROJECT_ROOT" ]]; then
  echo "extract_deps.sh: répertoire introuvable: $PROJECT_ROOT" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "extract_deps.sh: python3 requis" >&2
  exit 1
fi

# Détermine le SHA courant si on est dans un git repo, sinon "unknown".
COMMIT_SHA=$(git -C "$PROJECT_ROOT" rev-parse HEAD 2>/dev/null || echo "unknown")
BRANCH=$(git -C "$PROJECT_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")

export PROJECT_ROOT REPO_FULL_NAME COMMIT_SHA BRANCH

python3 - <<'PYEOF'
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(os.environ["PROJECT_ROOT"]).resolve()
REPO = os.environ["REPO_FULL_NAME"]
SHA = os.environ["COMMIT_SHA"]
BRANCH = os.environ["BRANCH"]

NS = {"m": "http://maven.apache.org/POM/4.0.0"}
NS41 = {"m": "http://maven.apache.org/POM/4.1.0"}
GROUP_PREFIX = "io.vidocq"

def ns_for(root):
    tag = root.tag
    if tag.startswith("{"):
        uri = tag[1:].split("}", 1)[0]
        return {"m": uri}
    return {}

def text(elem, tag, ns):
    if elem is None:
        return None
    if not ns:
        node = elem.find(tag)
    else:
        node = elem.find(f"m:{tag}", ns)
    return node.text.strip() if node is not None and node.text else None

def find_all(elem, tag, ns):
    if elem is None:
        return []
    if not ns:
        return list(elem.findall(tag))
    return list(elem.findall(f"m:{tag}", ns))

def load_pom(path):
    """Retourne (root, ns) ou (None, None) si parse impossible."""
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        return root, ns_for(root)
    except ET.ParseError as exc:
        print(f"extract_deps.sh: pom illisible ({path}): {exc}", file=sys.stderr)
        return None, None

def get_modules(root, ns):
    """Maven 4 supporte <modules> ET <subprojects>."""
    out = []
    for container in ("modules", "subprojects"):
        node = root.find(f"m:{container}", ns) if ns else root.find(container)
        if node is None:
            continue
        for child in list(node):
            tag = child.tag.split("}", 1)[-1]
            if tag in ("module", "subproject") and child.text:
                out.append(child.text.strip())
    return out


def get_packaging(root, ns):
    return text(root, "packaging", ns) or "jar"


def discover_modules(pom_path):
    """Découverte auto Maven 4 : si packaging=pom et pas de <modules>/<subprojects>,
    scan tous les sous-répertoires immédiats contenant un pom.xml.
    """
    parent_dir = pom_path.parent
    skip_dirs = {"target", ".git", ".idea", ".mvn", "node_modules", "build", "out"}
    out = []
    if not parent_dir.is_dir():
        return out
    for child in sorted(parent_dir.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name in skip_dirs:
            continue
        if (child / "pom.xml").is_file():
            out.append(child.name)
    return out

def coords(root, ns, parent_group=None, parent_version=None):
    g = text(root, "groupId", ns) or parent_group
    a = text(root, "artifactId", ns)
    v = text(root, "version", ns) or parent_version
    return g, a, v

def parent_coords(root, ns):
    p = root.find("m:parent", ns) if ns else root.find("parent")
    if p is None:
        return None, None
    return text(p, "groupId", ns), text(p, "version", ns)

def collect_properties(root, ns):
    props = {}
    p = root.find("m:properties", ns) if ns else root.find("properties")
    if p is None:
        return props
    for child in list(p):
        tag = child.tag.split("}", 1)[-1]
        if child.text:
            props[tag] = child.text.strip()
    return props

def resolve(value, props, depth=0):
    if value is None or depth > 8:
        return value
    out = value
    while "${" in out:
        start = out.find("${")
        end = out.find("}", start)
        if end == -1:
            break
        key = out[start + 2:end]
        if key in props:
            out = out[:start] + props[key] + out[end + 1:]
        else:
            return out  # variable inconnue, on laisse en l'état
    return out

def walk_modules(root_pom):
    """Retourne la liste de tous les pom.xml du reactor (racine inclus), en suivant <modules>/<subprojects>."""
    visited = []
    stack = [root_pom]
    seen = set()
    while stack:
        pom = stack.pop().resolve()
        if str(pom) in seen or not pom.is_file():
            continue
        seen.add(str(pom))
        visited.append(pom)
        root, ns = load_pom(pom)
        if root is None:
            continue
        modules = get_modules(root, ns)
        if not modules and get_packaging(root, ns) == "pom":
            # Maven 4 : découverte auto si pas de <modules>/<subprojects>.
            modules = discover_modules(pom)
        for mod in modules:
            child_dir = (pom.parent / mod).resolve()
            child_pom = child_dir / "pom.xml" if child_dir.is_dir() else child_dir
            if child_pom.suffix != ".xml":
                child_pom = child_pom.with_suffix(".xml")
            if child_pom.is_file():
                stack.append(child_pom)
    return visited

def is_vidocq(group):
    if not group:
        return False
    return group == GROUP_PREFIX or group.startswith(GROUP_PREFIX + ".")

# 1. Trouver le pom racine
root_pom = PROJECT_ROOT / "pom.xml"
if not root_pom.is_file():
    print(f"extract_deps.sh: pas de pom.xml racine dans {PROJECT_ROOT}", file=sys.stderr)
    sys.exit(1)

poms = walk_modules(root_pom)

produces = []
all_deps = []

for pom in poms:
    root, ns = load_pom(pom)
    if root is None:
        continue
    pg, pv = parent_coords(root, ns)
    g, a, v = coords(root, ns, pg, pv)
    if a is None:
        continue
    props = collect_properties(root, ns)
    # Propriétés implicites Maven
    if v:
        props.setdefault("project.version", v)
        props.setdefault("project.parent.version", pv or v)
    if g:
        props.setdefault("project.groupId", g)
    g_r = resolve(g, props)
    v_r = resolve(v, props)
    if is_vidocq(g_r):
        produces.append({"groupId": g_r, "artifactId": a, "version": v_r or ""})
    # Dépendances : <dependencies>/<dependency> au top-level + dans <dependencyManagement>
    for container in ("dependencies", "dependencyManagement"):
        node = root.find(f"m:{container}", ns) if ns else root.find(container)
        if node is None:
            continue
        if container == "dependencyManagement":
            node = node.find("m:dependencies", ns) if ns else node.find("dependencies")
            if node is None:
                continue
        for dep in find_all(node, "dependency", ns):
            dg = resolve(text(dep, "groupId", ns), props)
            da = text(dep, "artifactId", ns)
            dv = resolve(text(dep, "version", ns), props)
            ds = text(dep, "scope", ns) or "compile"
            dt = text(dep, "type", ns) or "jar"
            if is_vidocq(dg) and da:
                all_deps.append({
                    "groupId": dg,
                    "artifactId": da,
                    "version": dv or "",
                    "scope": ds,
                    "type": dt,
                })
    # Plugins Vidocq déclarés dans <build>/<plugins>
    for build_tag in ("build", "reporting"):
        b = root.find(f"m:{build_tag}", ns) if ns else root.find(build_tag)
        if b is None:
            continue
        plugins_node = b.find("m:plugins", ns) if ns else b.find("plugins")
        if plugins_node is None:
            pm = b.find("m:pluginManagement", ns) if ns else b.find("pluginManagement")
            if pm is not None:
                plugins_node = pm.find("m:plugins", ns) if ns else pm.find("plugins")
        if plugins_node is None:
            continue
        for plugin in find_all(plugins_node, "plugin", ns):
            pg2 = resolve(text(plugin, "groupId", ns), props)
            pa2 = text(plugin, "artifactId", ns)
            pv2 = resolve(text(plugin, "version", ns), props)
            if is_vidocq(pg2) and pa2:
                all_deps.append({
                    "groupId": pg2,
                    "artifactId": pa2,
                    "version": pv2 or "",
                    "scope": "build",
                    "type": "maven-plugin",
                })

# Déduplication des produces (même module peut apparaître via plusieurs poms)
prod_keys = set()
prod_unique = []
for p in produces:
    k = (p["groupId"], p["artifactId"])
    if k in prod_keys:
        continue
    prod_keys.add(k)
    prod_unique.append(p)
prod_unique.sort(key=lambda d: (d["groupId"], d["artifactId"]))

# Filtrage des consumes : on retire les artefacts produits par ce repo
consume_unique = []
seen = set()
for d in all_deps:
    k = (d["groupId"], d["artifactId"], d["scope"])
    if (d["groupId"], d["artifactId"]) in prod_keys:
        continue
    if k in seen:
        continue
    seen.add(k)
    consume_unique.append(d)
consume_unique.sort(key=lambda d: (d["groupId"], d["artifactId"], d["scope"]))

now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

out = {
    "schema_version": 1,
    "repo": REPO,
    "branch": BRANCH,
    "commit_sha": SHA,
    "updated_at": now_iso,
    "produces": prod_unique,
    "consumes": consume_unique,
}

json.dump(out, sys.stdout, indent=2, sort_keys=False)
sys.stdout.write("\n")
PYEOF
