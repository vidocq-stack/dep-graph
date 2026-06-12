"""Tests unitaires pour build_inverted_graph et resolve_impact."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

# Ajoute le répertoire parent (scripts/) au path pour importer les modules.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import build_inverted_graph as builder  # noqa: E402
import resolve_impact as resolver  # noqa: E402


def make_payload(repo, produces=None, consumes=None):
    return {
        "schema_version": 1,
        "repo": repo,
        "branch": "main",
        "commit_sha": "abc",
        "updated_at": "2026-05-10T00:00:00Z",
        "produces": produces or [],
        "consumes": consumes or [],
    }


def art(g, a, version="1.0.0-SNAPSHOT", scope="compile"):
    return {"groupId": g, "artifactId": a, "version": version, "scope": scope}


def prod(g, a, version="1.0.0-SNAPSHOT"):
    return {"groupId": g, "artifactId": a, "version": version}


def write_payloads(tmp: Path, payloads: list[tuple[str, dict]]) -> None:
    for fname, p in payloads:
        (tmp / fname).write_text(json.dumps(p), encoding="utf-8")


class BuildGraphTests(unittest.TestCase):
    def test_empty(self):
        graph = builder.build([])
        self.assertEqual(graph["artifacts"], {})
        self.assertEqual(graph["schema_version"], 1)

    def test_simple_chain(self):
        # A -> B -> C : vidocq/a produit io.vidocq.a:core, vidocq/b consomme et produit
        # io.vidocq.b:core, vidocq/c consomme io.vidocq.b:core.
        payloads = [
            make_payload("vidocq/a", produces=[prod("io.vidocq.a", "core")]),
            make_payload(
                "vidocq/b",
                produces=[prod("io.vidocq.b", "core")],
                consumes=[art("io.vidocq.a", "core")],
            ),
            make_payload("vidocq/c", consumes=[art("io.vidocq.b", "core")]),
        ]
        graph = builder.build(payloads)
        a_entry = graph["artifacts"]["io.vidocq.a:core"]
        self.assertEqual(a_entry["produced_by"], "vidocq/a")
        self.assertEqual(a_entry["consumed_by"], [{"repo": "vidocq/b", "scope": "compile"}])
        b_entry = graph["artifacts"]["io.vidocq.b:core"]
        self.assertEqual(b_entry["produced_by"], "vidocq/b")
        self.assertEqual(b_entry["consumed_by"], [{"repo": "vidocq/c", "scope": "compile"}])

    def test_diamond(self):
        # A -> B, A -> C, B -> D, C -> D
        payloads = [
            make_payload("vidocq/a", produces=[prod("io.vidocq.a", "core")]),
            make_payload(
                "vidocq/b",
                produces=[prod("io.vidocq.b", "core")],
                consumes=[art("io.vidocq.a", "core")],
            ),
            make_payload(
                "vidocq/c",
                produces=[prod("io.vidocq.c", "core")],
                consumes=[art("io.vidocq.a", "core")],
            ),
            make_payload(
                "vidocq/d",
                consumes=[art("io.vidocq.b", "core"), art("io.vidocq.c", "core")],
            ),
        ]
        graph = builder.build(payloads)
        impacted = resolver.resolve_impact(graph, ["io.vidocq.a:core"], max_depth=None)
        # Un changement dans A doit impacter B, C et D (transitif).
        self.assertEqual(impacted, ["vidocq/b", "vidocq/c", "vidocq/d"])

    def test_depth_limit(self):
        payloads = [
            make_payload("vidocq/a", produces=[prod("io.vidocq.a", "core")]),
            make_payload(
                "vidocq/b",
                produces=[prod("io.vidocq.b", "core")],
                consumes=[art("io.vidocq.a", "core")],
            ),
            make_payload("vidocq/c", consumes=[art("io.vidocq.b", "core")]),
        ]
        graph = builder.build(payloads)
        depth1 = resolver.resolve_impact(graph, ["io.vidocq.a:core"], max_depth=1)
        self.assertEqual(depth1, ["vidocq/b"])
        depth_all = resolver.resolve_impact(graph, ["io.vidocq.a:core"], max_depth=None)
        self.assertEqual(depth_all, ["vidocq/b", "vidocq/c"])

    def test_cycle_warning_no_crash(self):
        payloads = [
            make_payload(
                "vidocq/a",
                produces=[prod("io.vidocq.a", "core")],
                consumes=[art("io.vidocq.b", "core")],
            ),
            make_payload(
                "vidocq/b",
                produces=[prod("io.vidocq.b", "core")],
                consumes=[art("io.vidocq.a", "core")],
            ),
        ]
        with self.assertLogs("build_inverted_graph", level="WARNING") as ctx:
            graph = builder.build(payloads)
        self.assertTrue(
            any("Cycle détecté" in line for line in ctx.output),
            f"attendu 'Cycle détecté' dans : {ctx.output}",
        )
        impacted = resolver.resolve_impact(graph, ["io.vidocq.a:core"], max_depth=None)
        self.assertEqual(impacted, ["vidocq/a", "vidocq/b"])

    def test_external_repo_filtered(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            write_payloads(data_dir, [
                ("vidocq_a.json", make_payload("vidocq/a", produces=[prod("io.vidocq.a", "core")])),
                ("external_b.json", make_payload("external/b", consumes=[art("io.vidocq.a", "core")])),
            ])
            with self.assertLogs("build_inverted_graph", level="WARNING") as ctx:
                payloads = builder.load_data_files(data_dir)
            self.assertEqual(len(payloads), 1)
            self.assertEqual(payloads[0]["repo"], "vidocq/a")
            self.assertTrue(
                any("hors whitelist" in line for line in ctx.output),
                f"attendu 'hors whitelist' dans : {ctx.output}",
            )


class TopologicalOrderTests(unittest.TestCase):
    """resolve_impact_ordered : les impactés triés producteur-avant-consommateur."""

    def test_diamond_leaf_last(self):
        # A -> B, A -> C, B -> D, C -> D : D (feuille) doit venir après B et C.
        payloads = [
            make_payload("vidocq/a", produces=[prod("io.vidocq.a", "core")]),
            make_payload(
                "vidocq/b",
                produces=[prod("io.vidocq.b", "core")],
                consumes=[art("io.vidocq.a", "core")],
            ),
            make_payload(
                "vidocq/c",
                produces=[prod("io.vidocq.c", "core")],
                consumes=[art("io.vidocq.a", "core")],
            ),
            make_payload(
                "vidocq/d",
                consumes=[art("io.vidocq.b", "core"), art("io.vidocq.c", "core")],
            ),
        ]
        graph = builder.build(payloads)
        order = resolver.resolve_impact_ordered(graph, ["io.vidocq.a:core"], None)
        self.assertEqual(set(order), {"vidocq/b", "vidocq/c", "vidocq/d"})
        self.assertEqual(order[-1], "vidocq/d", f"D doit être dernier, ordre={order}")

    def test_topo_differs_from_alpha(self):
        # root -> z-lib -> a-app : l'ordre alpha [a-app, z-lib] est FAUX,
        # le producteur z-lib doit précéder son consommateur a-app.
        payloads = [
            make_payload("vidocq/root", produces=[prod("io.vidocq.root", "core")]),
            make_payload(
                "vidocq/z-lib",
                produces=[prod("io.vidocq.z", "core")],
                consumes=[art("io.vidocq.root", "core")],
            ),
            make_payload(
                "vidocq/a-app",
                consumes=[art("io.vidocq.z", "core")],
            ),
        ]
        graph = builder.build(payloads)
        impacted = resolver.resolve_impact(graph, ["io.vidocq.root:core"], None)
        self.assertEqual(impacted, ["vidocq/a-app", "vidocq/z-lib"])  # ordre alpha
        order = resolver.resolve_impact_ordered(graph, ["io.vidocq.root:core"], None)
        self.assertEqual(order, ["vidocq/z-lib", "vidocq/a-app"])  # ordre topo

    def test_cycle_no_crash(self):
        # A <-> B cyclique : pas de crash, les deux repos restent présents.
        payloads = [
            make_payload(
                "vidocq/a",
                produces=[prod("io.vidocq.a", "core")],
                consumes=[art("io.vidocq.b", "core")],
            ),
            make_payload(
                "vidocq/b",
                produces=[prod("io.vidocq.b", "core")],
                consumes=[art("io.vidocq.a", "core")],
            ),
        ]
        graph = builder.build(payloads)
        order = resolver.resolve_impact_ordered(graph, ["io.vidocq.a:core"], None)
        self.assertEqual(set(order), {"vidocq/a", "vidocq/b"})


class ResolveImpactCliTests(unittest.TestCase):
    def test_matrix_format(self):
        graph = builder.build(
            [
                make_payload("vidocq/a", produces=[prod("io.vidocq.a", "core")]),
                make_payload("vidocq/b", consumes=[art("io.vidocq.a", "core")]),
            ]
        )
        impacted = resolver.resolve_impact(graph, ["io.vidocq.a:core"], None)
        matrix = {"include": [{"repo": r} for r in impacted]}
        self.assertEqual(matrix, {"include": [{"repo": "vidocq/b"}]})


class CheckModeTests(unittest.TestCase):
    """Le sous-mode --check : exit 0 sur graphe sain, exit 1 sur warning."""

    def test_check_orphan_returns_1(self):
        # vidocq/b consomme io.vidocq.a:core mais aucun repo ne le produit -> orphelin.
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            write_payloads(data_dir, [
                ("vidocq_b.json", make_payload(
                    "vidocq/b", consumes=[art("io.vidocq.a", "core")]
                )),
            ])
            code, warnings = builder.run_check(data_dir)
        self.assertEqual(code, 1, "un orphelin doit faire échouer --check")
        self.assertGreaterEqual(warnings, 1)

    def test_check_clean_graph_returns_0(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            write_payloads(data_dir, [
                ("vidocq_a.json", make_payload(
                    "vidocq/a", produces=[prod("io.vidocq.a", "core")]
                )),
                ("vidocq_b.json", make_payload(
                    "vidocq/b",
                    produces=[prod("io.vidocq.b", "core")],
                    consumes=[art("io.vidocq.a", "core")],
                )),
            ])
            code, warnings = builder.run_check(data_dir)
        self.assertEqual(code, 0, f"graphe sain attendu, warnings={warnings}")
        self.assertEqual(warnings, 0)


if __name__ == "__main__":
    unittest.main()
