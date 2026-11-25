import pytest

from devloop import cron


@pytest.fixture(autouse=True)
def env(fakebin, tmp_path, monkeypatch, proj):
    monkeypatch.setenv("FAKE_CRONTAB", str(tmp_path / "crontab"))
    monkeypatch.delenv("FAKE_CRONTAB_ERR", raising=False)


def table(tmp_path):
    p = tmp_path / "crontab"
    return p.read_text() if p.exists() else ""


def test_cron_add_is_idempotent_and_keeps_others(proj, tmp_path):
    (tmp_path / "crontab").write_text("0 3 * * * backup\n# note\n")
    cron.add(proj, "/opt/bin")
    cron.add(proj, "/opt/bin")
    text = table(tmp_path)
    assert text.count("# devloop:p") == 1
    assert "0 3 * * * backup" in text and "# note" in text
    line = next(x for x in text.splitlines() if x.endswith("# devloop:p"))
    assert line.startswith("*/30 * * * * PATH=") and "HOME=" in line
    assert "DEVLOOP_HEADLESS=1 /opt/bin/devloop-tick p" in line
    assert (tmp_path / "home" / "p" / "crontab.bak").read_text().count(
        "# devloop:p"
    ) == 1


def test_cron_remove_keeps_others(proj, tmp_path):
    (tmp_path / "crontab").write_text("0 3 * * * backup\n")
    cron.add(proj, "/opt/bin")
    cron.remove(proj)
    assert table(tmp_path) == "0 3 * * * backup\n"
    cron.remove(proj)  # nothing to remove is fine
    assert table(tmp_path) == "0 3 * * * backup\n"


def test_cron_tag_matches_exactly(proj, tmp_path):
    (tmp_path / "crontab").write_text("* * * * * x # devloop:pp\n")
    cron.add(proj, "/opt/bin")
    assert "# devloop:pp" in table(tmp_path)
    cron.remove(proj)
    assert table(tmp_path) == "* * * * * x # devloop:pp\n"


def test_cron_empty_crontab_is_only_the_no_crontab_message(proj, tmp_path, monkeypatch):
    cron.add(proj, "/opt/bin")  # fake prints "no crontab for tester" when absent
    assert "# devloop:p" in table(tmp_path)


def test_cron_read_error_is_not_empty(proj, tmp_path, monkeypatch):
    (tmp_path / "crontab").write_text("0 3 * * * backup\n")
    monkeypatch.setenv("FAKE_CRONTAB_ERR", "crontab: permission denied")
    with pytest.raises(cron.CronError):
        cron.add(proj, "/opt/bin")
    monkeypatch.delenv("FAKE_CRONTAB_ERR")
    assert table(tmp_path) == "0 3 * * * backup\n"


def test_cron_restores_when_the_readback_differs(proj, tmp_path, monkeypatch):
    (tmp_path / "crontab").write_text("0 3 * * * backup\n")
    real = cron._write
    seen = []

    def lossy(text):
        seen.append(text)
        real("" if len(seen) == 1 else text)

    monkeypatch.setattr(cron, "_write", lossy)
    with pytest.raises(cron.CronError, match="old one is back"):
        cron.add(proj, "/opt/bin")
    assert table(tmp_path) == "0 3 * * * backup\n"
