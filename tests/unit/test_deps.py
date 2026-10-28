"""The dependency probes decide whether the app looks broken or helpful."""

from __future__ import annotations

import pytest

from src.engine import deps
from src.engine.errors import DependencyMissingError


@pytest.mark.unit
def test_require_imports_a_real_module() -> None:
    module = deps.require("json")
    assert hasattr(module, "dumps")


@pytest.mark.unit
def test_require_raises_with_install_hint() -> None:
    with pytest.raises(DependencyMissingError) as info:
        deps.require("definitely_not_a_module_xyz", pip_name="mystery-pkg", feature="Testing")
    message = info.value.user_message
    assert "mystery-pkg" in message
    assert "pip install mystery-pkg" in message
    assert "Testing" in message


@pytest.mark.unit
def test_require_falls_back_to_module_name_in_hint() -> None:
    with pytest.raises(DependencyMissingError) as info:
        deps.require("no_such_module_abc")
    assert "no_such_module_abc" in info.value.user_message


@pytest.mark.unit
def test_require_wraps_an_oserror_from_require(monkeypatch) -> None:
    """``require`` should classify a broken native import too."""

    def broken(name: str, *args, **kwargs):
        if name == "somepkg":
            raise OSError("cannot open shared object file")
        return real_import(name)

    real_import = deps.importlib.import_module
    monkeypatch.setattr(deps.importlib, "import_module", broken)

    with pytest.raises(DependencyMissingError) as info:
        deps.require("somepkg", pip_name="SomePkg", feature="A feature")

    message = info.value.user_message
    assert "SomePkg" in message
    assert "force-reinstall" in message
    # The original error must survive for diagnostics.
    assert isinstance(info.value.__cause__, OSError)
