"""The generalization proof: unchanged code passes, any edit to agent/ or auditor/ fails, data edits are listed."""
import pytest

from scripts import prove_generalization as pg


@pytest.fixture
def repo(tmp_path):
    for rel, text in {
        "agent/loop.py": "print('loop')\n", "agent/sub/tools.py": "x = 1\n", "auditor/engine.py": "y = 2\n",
        "company_pack/sops/a.md": "rule\n", "evals/scenarios.yaml": "scenarios: []\n", "apps/company/world.py": "SEED = 1\n",
        "inputs/form.txt": "form\n", "agent/__pycache__/loop.cpython-311.pyc": "junk",
    }.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    pg.freeze(tmp_path)
    return tmp_path


def run(root, capsys):
    code = pg.main(["--root", str(root)])
    return code, capsys.readouterr().out


def test_unchanged_code_passes_and_caches_are_ignored(repo, capsys):
    (repo / "agent" / "__pycache__" / "other.pyc").write_text("more junk")
    code, out = run(repo, capsys)
    assert code == 0 and "CODE UNCHANGED: all 3 files" in out and "DATA: nothing changed" in out


def test_any_edit_to_the_code_fails_and_names_the_file(repo, capsys):
    (repo / "auditor" / "engine.py").write_text("y = 3\n")
    code, out = run(repo, capsys)
    assert code == 1 and "does NOT hold" in out and "changed: auditor/engine.py" in out


def test_added_and_removed_code_files_fail_too(repo, capsys):
    (repo / "agent" / "new_tool.py").write_text("z = 1\n")
    (repo / "agent" / "sub" / "tools.py").unlink()
    code, out = run(repo, capsys)
    assert code == 1 and "added: agent/new_tool.py" in out and "removed: agent/sub/tools.py" in out


def test_new_data_is_listed_but_does_not_fail(repo, capsys):
    (repo / "company_pack" / "sops" / "b.md").write_text("new rule\n")
    (repo / "evals" / "scenarios.yaml").write_text("scenarios: [S6]\n")
    code, out = run(repo, capsys)
    assert code == 0 and "CODE UNCHANGED" in out
    assert "added: company_pack/sops/b.md" in out and "changed: evals/scenarios.yaml" in out and "(2 files)" in out


def test_line_endings_alone_are_not_a_change(repo, capsys):
    (repo / "agent" / "loop.py").write_bytes(b"print('loop')\r\n")
    assert run(repo, capsys)[0] == 0


def test_without_a_freeze_record_it_says_so(tmp_path, capsys):
    code, out = run(tmp_path, capsys)
    assert code == 1 and "No freeze record" in out
