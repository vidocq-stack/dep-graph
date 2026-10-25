"""Tests for extract_deps.sh: every io.vidocq artifact a build can pull in is recorded as consumed.

Run: python3 -m unittest discover -s scripts/tests
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "extract_deps.sh"

HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
"""


def consumed(project: Path) -> set[tuple[str, str]]:
    out = subprocess.run(["bash", str(SCRIPT), str(project), "Vidocq/demo"],
                         check=True, capture_output=True, text=True).stdout
    return {(c["artifactId"], c["scope"]) for c in json.loads(out)["consumes"]}


def project(tmp: str, body: str) -> Path:
    root = Path(tmp)
    (root / "pom.xml").write_text(
        HEAD + "<groupId>io.vidocq.demo</groupId><artifactId>demo</artifactId>"
        "<version>1.0.0-SNAPSHOT</version>\n" + body + "\n</project>\n", encoding="utf-8")
    return root


class ExtractDepsTest(unittest.TestCase):

    def test_plain_dependency_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp, """<dependencies><dependency><groupId>io.vidocq.vauban</groupId>
              <artifactId>vauban-core</artifactId><version>1</version></dependency></dependencies>""")
            self.assertIn(("vauban-core", "compile"), consumed(root))

    def test_annotation_processor_path_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp, """<build><plugins><plugin>
              <groupId>org.apache.maven.plugins</groupId><artifactId>maven-compiler-plugin</artifactId>
              <configuration><annotationProcessorPaths><path><groupId>io.vidocq.vauban</groupId>
              <artifactId>vauban-processor</artifactId><version>1</version></path>
              </annotationProcessorPaths></configuration></plugin></plugins></build>""")
            self.assertIn(("vauban-processor", "build"), consumed(root))

    def test_plugin_dependency_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp, """<build><plugins><plugin>
              <groupId>org.example</groupId><artifactId>some-plugin</artifactId>
              <dependencies><dependency><groupId>io.vidocq.peano</groupId>
              <artifactId>peano-processor</artifactId><version>1</version></dependency></dependencies>
              </plugin></plugins></build>""")
            self.assertIn(("peano-processor", "build"), consumed(root))

    def test_plugin_management_is_read_next_to_plugins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp, """<build>
              <pluginManagement><plugins><plugin><groupId>io.vidocq.vauban</groupId>
                <artifactId>vauban-maven-plugin</artifactId><version>1</version></plugin></plugins>
              </pluginManagement>
              <plugins><plugin><groupId>io.vidocq.ibarra</groupId>
                <artifactId>ibarra-maven-plugin</artifactId><version>1</version></plugin></plugins>
            </build>""")
            got = consumed(root)
            self.assertIn(("vauban-maven-plugin", "build"), got)
            self.assertIn(("ibarra-maven-plugin", "build"), got)

    def test_profile_dependencies_and_plugins_are_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp, """<profiles><profile><id>it</id>
              <dependencies><dependency><groupId>io.vidocq.knock</groupId>
                <artifactId>knock-core</artifactId><version>1</version><scope>test</scope></dependency>
              </dependencies>
              <build><plugins><plugin>
                <groupId>org.apache.maven.plugins</groupId><artifactId>maven-compiler-plugin</artifactId>
                <configuration><annotationProcessorPaths><path><groupId>io.vidocq.cyrano</groupId>
                <artifactId>cyrano-processor</artifactId><version>1</version></path>
                </annotationProcessorPaths></configuration></plugin></plugins></build>
            </profile></profiles>""")
            got = consumed(root)
            self.assertIn(("knock-core", "test"), got)
            self.assertIn(("cyrano-processor", "build"), got)

    def test_non_vidocq_processor_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = project(tmp, """<build><plugins><plugin>
              <groupId>org.apache.maven.plugins</groupId><artifactId>maven-compiler-plugin</artifactId>
              <configuration><annotationProcessorPaths><path><groupId>org.example</groupId>
              <artifactId>other-processor</artifactId><version>1</version></path>
              </annotationProcessorPaths></configuration></plugin></plugins></build>""")
            self.assertEqual(set(), consumed(root))


if __name__ == "__main__":
    unittest.main()
