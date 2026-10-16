import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class DockerPackagingTests(unittest.TestCase):
    def test_docker_context_includes_tests_when_build_runs_them(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        ignored = {
            line.strip().rstrip("/")
            for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        if "unittest discover -s tests" in dockerfile:
            self.assertNotIn("tests", ignored)


if __name__ == "__main__":
    unittest.main()
