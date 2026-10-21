"""Pure helpers behind the ribbon Atualizar button."""
from revit_mcp import self_update


def test_commit_lines_uses_first_line_and_limits():
    messages = ["fix: a\n\nbody", "feat: b", ""] + ["chore: x"] * 10
    text = self_update.commit_lines(messages, limit=3)
    assert text.splitlines() == [u"• fix: a", u"• feat: b",
                                 u"• (sem mensagem)", u"• ... e mais 10"]
    assert self_update.commit_lines([]) == u""


def test_result_message_reports_server_restart():
    text = self_update.result_message("a" * 40, "b" * 40, True, True, "ready")
    assert "aaaaaaa" in text and "bbbbbbb" in text and "reiniciado" in text
    slow = self_update.result_message("a" * 40, "b" * 40, True, False, "still starting")
    assert "still starting" in slow
    assert u"remoto" not in self_update.result_message("a", "b", False, False, "")


def _git_tree(tmp_path, head, refs=None, packed=None):
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text(head)
    for name, sha in (refs or {}).items():
        path = git.joinpath(*name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sha + "\n")
    if packed:
        (git / "packed-refs").write_text(packed)
    return str(tmp_path)


def test_installed_commit_reads_loose_packed_and_detached(tmp_path):
    (tmp_path / "a").mkdir()
    loose = _git_tree(tmp_path / "a", "ref: refs/heads/master\n",
                      refs={"refs/heads/master": "a" * 40})
    assert self_update.installed_commit(loose) == "a" * 40
    (tmp_path / "b").mkdir()
    packed = _git_tree(tmp_path / "b", "ref: refs/heads/master\n",
                       packed="# pack-refs\n" + "b" * 40 + " refs/heads/master\n")
    assert self_update.installed_commit(packed) == "b" * 40
    (tmp_path / "c").mkdir()
    assert self_update.installed_commit(_git_tree(tmp_path / "c", "c" * 40)) == "c" * 40
    assert self_update.installed_commit(str(tmp_path / "missing")) is None


def test_server_is_stale():
    assert self_update.server_is_stale({"pid": 1}, "abc")             # started before tracking
    assert self_update.server_is_stale({"commit": "old"}, "abc")
    assert not self_update.server_is_stale({"commit": "abc"}, "abc")
    assert not self_update.server_is_stale(None, "abc")
    assert not self_update.server_is_stale({"pid": 1}, None)
