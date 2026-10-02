import json
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

from trade_helper import product_version as product_metadata


@pytest.mark.parametrize("installed,expected", [
    ("0.3.0", "0.3.0"),
    ("0.3.0b1", "0.3.0-beta.1"),
    ("0.3.0b12", "0.3.0-beta.12"),
])
def test_installed_product_metadata_uses_the_public_version(monkeypatch, installed, expected):
    def read_version(name):
        assert name == "tx-trade-api"
        return installed

    monkeypatch.setattr(product_metadata, "version", read_version)
    assert product_metadata.product_version() == expected


def test_uninstalled_source_does_not_claim_a_release_version(monkeypatch):
    def unavailable(_name):
        raise PackageNotFoundError

    monkeypatch.setattr(product_metadata, "version", unavailable)
    assert product_metadata.product_version() == "development"


def test_api_metadata_matches_the_current_product_manifest():
    from trade_helper.api import app

    manifest = json.loads((Path(__file__).resolve().parents[3] / "version.json").read_text())
    assert app.version == manifest["version"]


def test_backend_version_command_does_not_require_a_desktop_session(monkeypatch, capsys):
    from trade_helper.desktop_runtime import main

    monkeypatch.delenv("APP_DESKTOP", raising=False)
    monkeypatch.delenv("APP_DESKTOP_TOKEN", raising=False)
    with pytest.raises(SystemExit) as exited:
        main(["--version"])
    assert exited.value.code == 0
    assert capsys.readouterr().out == f"txinTrade {product_metadata.product_version()}\n"
