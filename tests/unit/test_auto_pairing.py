# -*- coding: utf-8 -*-
"""Automatic pairing at load, respecting an explicit Disconnect."""
import os

import pytest

from revit_mcp import pairing


@pytest.fixture
def user(monkeypatch, tmp_path):
    # Identity "encryption" so the logic runs off Windows.
    monkeypatch.setattr(pairing, "_dpapi", lambda data, protect: data)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return tmp_path


def test_first_load_pairs_automatically(user):
    assert not pairing.is_paired()
    assert pairing.auto_pair() is True
    assert pairing.is_paired()


def test_existing_pairing_is_kept(user):
    pairing.enable_pairing()
    token = pairing.get_token()
    assert pairing.auto_pair() is False
    assert pairing.get_token() == token


def test_disconnect_is_respected_until_connect(user):
    pairing.enable_pairing()
    pairing.disable_pairing()
    assert pairing.auto_pair() is False
    assert not pairing.is_paired()
    pairing.enable_pairing()          # Connect button
    assert pairing.is_paired()
    assert pairing.auto_pair() is False


def test_connect_clears_the_opt_out(user):
    pairing.disable_pairing()
    assert os.path.isfile(pairing.opt_out_path())
    pairing.enable_pairing()
    assert not os.path.isfile(pairing.opt_out_path())
