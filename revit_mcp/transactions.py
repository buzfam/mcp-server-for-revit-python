# -*- coding: UTF-8 -*-
"""
Shared transaction helpers for write routes.

Every write goes through a TransactionGroup with one Transaction inside it.
The transaction has a failures preprocessor, so Revit warnings are collected
(and deleted) instead of opening dialogs that would block the HTTP request,
and error-severity failures roll the transaction back. A dry run commits the
inner transaction (so Revit runs its failure processing) and then rolls the
group back; a real run assimilates the group into a single undo step.

pyrevit's DB is imported inside the functions, so this module can be
imported without Revit (pure helpers are checked by scripts/ironpython_check.py).
"""

from .utils import normalize_string

MAX_WARNINGS = 50


def add_unique(items, text):
    """Append text to items unless it is already there. Returns items."""
    if text not in items:
        items.append(text)
    return items


def _db():
    from pyrevit import DB
    return DB


def failure_collector():
    """IFailuresPreprocessor that records warnings (and deletes them so no dialog
    blocks the request) and rolls back on errors. Built lazily: it needs the real API."""
    DB = _db()

    class FailureCollector(DB.IFailuresPreprocessor):
        def __init__(self):
            self.warnings = []
            self.errors = []

        def PreprocessFailures(self, accessor):
            has_error = False
            for message in accessor.GetFailureMessages():
                text = normalize_string(message.GetDescriptionText())
                if message.GetSeverity() == DB.FailureSeverity.Warning:
                    add_unique(self.warnings, text)
                    accessor.DeleteWarning(message)
                else:
                    add_unique(self.errors, text)
                    has_error = True
            if has_error:
                return DB.FailureProcessingResult.ProceedWithRollBack
            return DB.FailureProcessingResult.Continue

    return FailureCollector()


class Batch(object):
    """TransactionGroup + Transaction + failure collector, used as:

        batch = Batch(doc, "MCP: ...")
        batch.start()
        try:
            ... changes ...
            if problem: batch.rollback()
            else: status = batch.finish(commit)
        except Exception:
            batch.abort()
            raise
    """

    def __init__(self, doc, name):
        DB = _db()
        self.collector = failure_collector()
        self.group = DB.TransactionGroup(doc, name)
        self.transaction = DB.Transaction(doc, name)

    def start(self):
        self.group.Start()
        options = self.transaction.GetFailureHandlingOptions()
        options.SetFailuresPreprocessor(self.collector)
        options.SetClearAfterRollback(True)
        self.transaction.SetFailureHandlingOptions(options)
        self.transaction.Start()

    def rollback(self):
        """Discard everything (an item failed before the commit)."""
        self.transaction.RollBack()
        self.group.RollBack()

    def finish(self, commit):
        """Commit the transaction (failure processing runs), then keep the group
        when commit is True or roll it back for a dry run.
        Returns True when Revit committed; False when it rolled back."""
        DB = _db()
        status = self.transaction.Commit()
        if status != DB.TransactionStatus.Committed:
            self.group.RollBack()
            self.status = status
            return False
        if commit:
            self.group.Assimilate()
        else:
            self.group.RollBack()
        return True

    def abort(self):
        """Roll back whatever is still open (used after an exception)."""
        if self.transaction.HasStarted() and not self.transaction.HasEnded():
            self.transaction.RollBack()
        if self.group.HasStarted() and not self.group.HasEnded():
            self.group.RollBack()

    @property
    def warnings(self):
        return self.collector.warnings[:MAX_WARNINGS]

    @property
    def errors(self):
        return self.collector.errors[:MAX_WARNINGS]
