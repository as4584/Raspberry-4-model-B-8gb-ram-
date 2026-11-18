"""#33: generating the sealed holdout must never echo its seed (stdout, stderr
or a coverage report), or anyone with the terminal/transcript can regenerate it."""
from lab.datagen import cli

SEED = 987654321


def test_holdout_seed_not_printed(tmp_path, capsys):
    cli.main(["--split", "holdout", "--seed", str(SEED), "--n", "20", "--out", str(tmp_path / "h.jsonl"),
              "--coverage-report", str(tmp_path / "cov.md")])
    out = capsys.readouterr()
    assert str(SEED) not in out.out + out.err
    assert str(SEED) not in (tmp_path / "cov.md").read_text(encoding="utf-8")
    assert '"count": 20' in out.out and '"sha256"' in out.out


def test_dev_split_still_reports_seed(tmp_path, capsys):
    cli.main(["--split", "dev", "--seed", "7", "--n", "10", "--out", str(tmp_path / "d.jsonl")])
    assert '"seed": 7' in capsys.readouterr().out
