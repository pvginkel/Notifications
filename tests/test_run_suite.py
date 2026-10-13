"""run-suite, the command modernApp.test runs: pytest's exit code, the JUnit file and the
SUITE_RESULT line the step counts, over a tree of its own."""

import subprocess
import sys
from pathlib import Path

RUN_SUITE = Path(__file__).parents[1] / "run-suite"


def run_suite(tree: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(RUN_SUITE),
            "--output-mode",
            "full",
            "--junitxml-dir",
            str(tree / "results"),
            "--retries",
            "2",
        ],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )


def make_tree(tmp_path: Path, tests: str, conftest: str = "") -> Path:
    tree = tmp_path / "tree"
    tree.mkdir()
    # Its own ini file makes the tree pytest's rootdir, whatever lies above it.
    (tree / "pytest.ini").write_text("[pytest]\n")
    (tree / "test_sample.py").write_text(tests)
    if conftest:
        (tree / "conftest.py").write_text(conftest)
    return tree


def result_lines(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith("===SUITE_RESULT:")]


def test_a_green_suite_exits_0_and_reports_its_counts(tmp_path):
    tree = make_tree(tmp_path, "def test_one():\n    pass\n\n\ndef test_two():\n    pass\n")

    result = run_suite(tree)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result_lines(result.stdout) == ["===SUITE_RESULT:pytest:2:0:0==="]
    assert (tree / "results" / "pytest.xml").is_file()


def test_a_red_suite_exits_1_and_counts_failures_and_errors_as_failed(tmp_path):
    tree = make_tree(
        tmp_path,
        "import pytest\n\n\n"
        "@pytest.fixture\n"
        "def broken():\n"
        "    raise RuntimeError('fixture')\n\n\n"
        "def test_passes():\n    pass\n\n\n"
        "def test_fails():\n    assert False\n\n\n"
        "def test_errors(broken):\n    pass\n\n\n"
        "@pytest.mark.skip(reason='skipped')\n"
        "def test_skipped():\n    pass\n",
    )

    result = run_suite(tree)

    assert result.returncode == 1, result.stdout + result.stderr
    assert result_lines(result.stdout) == ["===SUITE_RESULT:pytest:1:2:1==="]


def test_a_suite_that_never_starts_prints_no_result_line(tmp_path):
    tree = make_tree(tmp_path, "def test_one():\n    pass\n", conftest="import missing_module\n")

    result = run_suite(tree)

    assert result.returncode == 4, result.stdout + result.stderr
    assert result_lines(result.stdout) == []
    assert not (tree / "results" / "pytest.xml").exists()
