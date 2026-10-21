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
