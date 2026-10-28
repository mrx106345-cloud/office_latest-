"""One named test per bug found while building YourDoc.

Each test states the symptom it locks down. If one of these starts failing,
the corresponding defect has regressed.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from src.engine import pdf_converters, pdf_core
from tests.support import builders


# --------------------------------------------------------------------------- #
# 1. TaskHandle was unhashable -> TaskRunner.submit() always raised TypeError
# --------------------------------------------------------------------------- #
@pytest.mark.regression
@pytest.mark.unit
def test_task_handle_is_hashable() -> None:
    """Symptom: running ANY tool crashed with 'unhashable type: TaskHandle'."""

    from src.engine.task_runner import TaskHandle, TaskRunner

    handle = TaskHandle(name="merge")
    # Must be usable as a set element: TaskRunner._active is a set of these.
    assert len({handle}) == 1
    # Equality is identity-based (eq=False), so two jobs with the same name
    # must not collapse into one entry in that set.
    other = TaskHandle(name="merge")
    assert len({handle, other}) == 2, "two handles must not collapse into one"
    assert handle != other

    runner = TaskRunner(max_workers=1)
    try:
        done: list[object] = []
        task = runner.submit("job", lambda p, c: "ok", on_done=done.append)
        assert task.wait(timeout=5)
        assert done == ["ok"]
    finally:
        runner.shutdown(wait=True)


# --------------------------------------------------------------------------- #
# 2. An auto-filled destination was mistaken for a user-typed one
# --------------------------------------------------------------------------- #
@pytest.mark.regression
@pytest.mark.ui
def test_settings_change_refreshes_auto_destination(shell, scratch: Path) -> None:
    """Symptom: after changing the output folder, tools kept the old default."""

    sample = builders.make_sample_pdf(scratch / "s.pdf", pages=1)
    view = shell.show("merge")
    view.receive_files([str(sample)])

    results = scratch / "new_results"
    settings = shell.show("__settings__")
    settings.next_to_source.set(False)
    settings.output_picker.set(str(results))
    settings.output_picker.set_enabled(True)
    settings.save()
    shell.root.update_idletasks()

    assert str(results) in view.output_var.get()


@pytest.mark.regression
@pytest.mark.ui
def test_user_typed_destination_is_never_clobbered(shell, scratch: Path) -> None:
    sample = builders.make_sample_pdf(scratch / "s.pdf", pages=1)
    chosen = scratch / "out" / "my_custom_name.pdf"

    view = shell.show("merge")
    view.receive_files([str(sample)])
    view.output_var.set(str(chosen))
    view.refresh_output_path()
    assert view.output_var.get() == str(chosen)


# --------------------------------------------------------------------------- #
# 3. pdf_info leaked raw pypdf errors
# --------------------------------------------------------------------------- #
@pytest.mark.regression
@pytest.mark.unit
def test_pdf_info_never_raises_when_reader_fails(
    scratch: Path, encrypted_pdf: Path, sample_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symptom: an unreadable file produced a traceback instead of a preview."""

    def boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("backend exploded")

    monkeypatch.setattr(pdf_core, "PdfReader", boom)
    locked = pdf_core.pdf_info(encrypted_pdf)
    assert locked["encrypted"] is True, "the /Encrypt tail scan should still detect it"
    assert pdf_core.pdf_info(sample_pdf)["pages"] == 0


# --------------------------------------------------------------------------- #
# 5. A relocated data folder was forgotten across a restart
# --------------------------------------------------------------------------- #
@pytest.mark.regression
@pytest.mark.unit
def test_relocated_data_folder_survives_reload(isolated_appdata: Path, scratch: Path) -> None:
    """Symptom: the data-folder setting vanished on the next launch."""

    from src.config import AppConfig, POINTER_FILENAME

    custom = scratch / "elsewhere"
    AppConfig(data_dir=str(custom), history_enabled=True).save(default_dir=isolated_appdata)
    assert (isolated_appdata / POINTER_FILENAME).read_text(encoding="utf-8").strip() == str(custom)

    reloaded = AppConfig.load(default_dir=isolated_appdata)
    assert reloaded.data_dir == str(custom)
    assert reloaded.history_enabled is True


# --------------------------------------------------------------------------- #
# 6. AppConfig.save() ignored the injected base directory
# --------------------------------------------------------------------------- #
@pytest.mark.regression
@pytest.mark.unit
def test_config_save_honours_injected_base(isolated_appdata: Path) -> None:
    """Symptom: tests and portable installs wrote to the real per-user folder."""

    from src.config import CONFIG_FILENAME, AppConfig

    written = AppConfig().save(default_dir=isolated_appdata)
    assert written == isolated_appdata / CONFIG_FILENAME
    assert written.is_file()


# --------------------------------------------------------------------------- #
# 7. The landing page handled mixed selections badly (route to Merge)
# --------------------------------------------------------------------------- #
@pytest.mark.regression
@pytest.mark.ui
def test_home_routes_selection_to_a_tool(shell, scratch: Path) -> None:
    a = builders.make_sample_pdf(scratch / "a.pdf", pages=1)
    b = builders.make_sample_pdf(scratch / "b.pdf", pages=1)
    home = shell.show("__home__")
    home._route_files([str(a), str(b)])  # noqa: SLF001
    shell.root.update_idletasks()
    view = shell._views.get("merge")  # noqa: SLF001
    assert view is not None and view.file_list.paths == [str(a), str(b)]


# --------------------------------------------------------------------------- #
# 8. The rebrand: a white-background JPEG would have shown as a bright plate
# --------------------------------------------------------------------------- #
@pytest.mark.regression
@pytest.mark.unit
def test_watermark_never_draws_the_logo_on_a_white_plate(tmp_path: Path) -> None:
    """Symptom (pre-fix): the logo's JPEG background covered the dark theme.

    Pryonx supplied the artwork as a JPEG on white. Dropping it in unchanged
    would have painted a light rectangle across the middle of the window, so the
    background is keyed out and the watermark is blended against the app colour.
    """

    from PIL import Image

    from src.ui import branding, theme

    image = branding.load_image(branding.WATERMARK_PNG)
    if image is None:  # pragma: no cover - artwork not generated
        pytest.skip("brand artwork is not generated in this checkout")

    background = branding.hex_to_rgb(theme.BG_APP)
    assert image.getchannel("A").getextrema() == (255, 255), "watermark must be opaque"

    # The corners of the artwork are empty space, so they must be exactly the
    # window colour rather than white.
    for point in ((0, 0), (image.width - 1, 0), (0, image.height - 1)):
        assert image.convert("RGB").getpixel(point) == background

    # And the mark itself must still be visible, just dimmed to 40%.
    opaque = branding.remove_background(
        Image.open(branding.source_path()).convert("RGB")
    ).convert("RGBA")
    assert opaque.getchannel("A").getextrema()[1] > 200


# --------------------------------------------------------------------------- #
# 9. Asset resolution ignored sys._MEIPASS (branding vanished when frozen)
# --------------------------------------------------------------------------- #
@pytest.mark.regression
@pytest.mark.unit
def test_branding_assets_resolve_inside_a_frozen_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Symptom: the installed app came up with no logo and no taskbar icon."""

    import sys

    from src.ui import branding

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)
    assert branding.assets_dir() == tmp_path / "_internal" / "assets"


@pytest.mark.regression
@pytest.mark.unit
def test_build_declares_the_asset_folder_for_packaging() -> None:
    """The PyInstaller command must actually carry assets/ into the bundle."""

    root = Path(__file__).resolve().parent.parent.parent
    source = (root / "build.py").read_text(encoding="utf-8")
    assert "_brand_data" in source
    assert "--add-data" in source
    assert "ASSETS_FOLDER" in source


# --------------------------------------------------------------------------- #
# 11. The installer carried a hardcoded version that drifted from app.py
# --------------------------------------------------------------------------- #
def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _app_version() -> str:
    """Read ``__version__`` from app.py without importing it (no Tk root)."""

    import ast

    source = (_repo_root() / "app.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "__version__" for t in node.targets
        ):
            return str(ast.literal_eval(node.value))
    raise AssertionError("app.py has no __version__ assignment")


@pytest.mark.regression
@pytest.mark.unit
def test_installer_has_no_hardcoded_version_fallback() -> None:
    """Regression: yourdoc.iss defaulted AppVersion to a literal.

    ``build_installer.py`` passes ``/DAppVersion=``, so the fallback only ever
    applied to a hand-run ``ISCC.exe`` invocation -- where it silently shipped
    the wrong version in the file name and the Apps & features entry. The
    script now raises a compile-time error instead.
    """

    script = (_repo_root() / "installer" / "yourdoc.iss").read_text(encoding="utf-8")
    assert "#ifndef AppVersion" in script, "the guard was removed"
    guard = script.split("#ifndef AppVersion", 1)[1].split("#endif", 1)[0]
    assert "#error" in guard, "AppVersion must fail loudly, not default"
    assert "#define AppVersion" not in guard, "a literal fallback is exactly the bug"

    # The example in the header comment must match the real version.
    assert f"/DAppVersion={_app_version()}" in script, (
        "the documented ISCC command line shows a stale version"
    )


@pytest.mark.regression
@pytest.mark.unit
def test_the_installer_gates_on_license_and_shows_the_eula() -> None:
    """YourDoc is commercial: setup must require a licence and a key.

    This inverts an older regression lock. While YourDoc was MIT-licensed free
    software, ``LicenseFile`` was deliberately absent - a wizard page holding
    the full MIT grant was worse than the single line it replaced, and every
    document still shipped into the install folder. That reasoning applied
    only to a free licence nobody has to accept to install the product. As a
    paid, per-machine product, EULA acceptance and a license key are the whole
    commercial point, so the wizard must show them and must not let setup
    finish without a key that verifies on this machine.

    The rest of the older concern still holds and is still checked: no wizard
    page may be turned into a dependency listing via ``isreadme``.
    """

    script = (_repo_root() / "installer" / "yourdoc.iss").read_text(encoding="utf-8")

    # Only the directives matter -- the comments above explain why each flag is
    # set the way it is, and a naive substring check would trip over its own
    # explanation.
    directives = [
        line
        for line in script.splitlines()
        if line.strip() and not line.lstrip().startswith(";")
    ]
    body = "\n".join(directives)
    assert "isreadme" not in body, (
        "a file is still shown on a wizard page, revealing the stack"
    )

    for name in (
        "THIRD_PARTY_NOTICES.md", "README.md", "LICENSE", "NOTICE", "EULA.txt",
    ):
        assert name in script, f"{name} must still ship into the install folder"

    assert "LicenseFile=..\\EULA.txt" in body, (
        "the EULA must be a real, required acceptance step, not just a file "
        "in the install folder"
    )

    # A key page must exist, must be reached after the EULA, and the real
    # verification must be delegated to the installed exe rather than
    # reimplemented in Pascal here.
    assert "CreateInputQueryPage(wpLicense" in body
    assert "--validate-key" in body, "setup must verify the key with the app"
    assert "--activate" in body, "setup must persist the key so the app opens activated"
    assert "ssPostInstall" in body


@pytest.mark.regression
@pytest.mark.unit
def test_the_project_is_proprietary_and_says_so_consistently() -> None:
    """YourDoc must ship under one licence, stated identically everywhere.

    Earlier releases were MIT, and AGPL-3.0 before that, when the app linked
    against AGPL-licensed PyMuPDF. Both are gone; the product is now
    proprietary commercial software sold one licence key per machine. A stale
    "MIT License" or "free software" string left anywhere would over-promise
    rights the EULA does not grant, exactly the same class of compliance bug
    the old MIT-consistency test guarded against -- just in the other
    direction now.
    """

    root = _repo_root()
    licence = (root / "LICENSE").read_text(encoding="utf-8")
    eula = (root / "EULA.txt").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    notices = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    shipped_notice = (root / "NOTICE").read_text(encoding="utf-8")

    assert "EULA.txt" in licence, "LICENSE must point at the full EULA text"
    for text, name in ((eula, "EULA.txt"), (licence, "LICENSE"), (readme, "README.md"), (notices, "THIRD_PARTY_NOTICES.md"), (shipped_notice, "NOTICE")):
        assert "MIT License" not in text or "Earlier releases" in text or "earlier releases" in text.lower(), (
            f"{name} still presents MIT as the current licence"
        )

    for text, name in ((eula, "EULA.txt"), (licence, "LICENSE"), (shipped_notice, "NOTICE")):
        assert "Pryonx" in text, f"{name} must hold the licence in Pryonx's name"

    # The clauses that make this a real, per-machine, per-term commercial
    # licence, not just a reworded MIT.
    assert "non-transferable" in eula
    assert "single computer" in eula.lower() or "one (1) copy" in eula
    assert "five (5) years" in eula or "five years" in eula.lower()
    assert "hardware fingerprint" in eula.lower() or "fingerprint" in eula.lower()
    assert "WITHOUT WARRANTY OF ANY KIND" in eula

    assert "GNU Affero General Public" not in readme
    assert "proprietary" in readme.lower()
    assert "licence key" in readme.lower() or "license key" in readme.lower()

    # Third-party notices must still record that YourDoc's own licence does
    # not reach the bundled components, and why the components are safe to
    # ship closed at all.
    assert "copyleft" in notices
    assert "PyMuPDF" in notices, "the removed dependency should be recorded"


@pytest.mark.regression
@pytest.mark.unit
def test_the_shipped_readme_is_for_end_users_not_developers() -> None:
    """The user-facing README must not read as a dependency manifest."""

    readme = (_repo_root() / "README.md").read_text(encoding="utf-8")

    for needle in ("pip install", "requirements.txt", "pyproject.toml",
                   "PyInstaller", "customtkinter", "openpyxl", "python-docx"):
        assert needle not in readme, (
            f"{needle!r} is developer material and does not belong in the "
            "README shipped to users"
        )

    # It must still say what the product is and how to get help.
    assert "YourDoc" in readme
    assert "info.pryonx@gmail.com" in readme or "Help" in readme

    # ...and point at the developer documentation instead.
    assert "DEVELOPING.md" in readme
    assert (_repo_root() / "DEVELOPING.md").is_file()


@pytest.mark.regression
@pytest.mark.unit
def test_build_installer_reads_the_version_from_app_py() -> None:
    """The helper must supply the version rather than relying on the script."""
    source = (_repo_root() / "build_installer.py").read_text(encoding="utf-8")
    assert "project_version" in source
    assert "/DAppVersion=" in source, "the version is not passed to ISCC"


@pytest.mark.regression
@pytest.mark.unit
def test_pyproject_version_tracks_app_py() -> None:
    """``app.py``'s ``__version__`` is the single source of truth.

    The installer script and the packaging metadata both have to agree with it,
    or a release ships labelled with the wrong number.
    """

    import tomllib

    root = _repo_root()
    pyproject = root / "pyproject.toml"
    assert pyproject.is_file(), "pyproject.toml is missing"
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    assert declared["version"] == _app_version(), (
        f"pyproject says {declared['version']}, app.py says {_app_version()}"
    )


@pytest.mark.regression
@pytest.mark.unit
def test_supported_python_range_is_declared() -> None:
    """The README claims 3.10+; the code must enforce it, not just state it.

    PEP 604 unions (``str | None``) are used throughout and need 3.10 or newer,
    so an unenforced claim means the app dies on import under an older runtime
    with a syntax error rather than a clear message.
    """

    import tomllib

    root = _repo_root()
    declared = tomllib.loads(
        (root / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    assert declared["requires-python"] == ">=3.10", (
        f"requires-python is {declared['requires-python']!r}"
    )


# --------------------------------------------------------------------------- #
# 10. The splash asked a withdrawn window for focus, so Escape did nothing
# --------------------------------------------------------------------------- #
@pytest.mark.regression
@pytest.mark.ui
def test_splash_binds_keys_on_the_card_not_only_the_window(tk_root) -> None:
    """Symptom: pressing Escape on the splash left it on screen.

    Tk delivers key events to the focused widget only, so binding the toplevel
    alone is not enough; each card widget carries the binding as well.
    """

    from src.ui import splash as splash_module

    screen = splash_module.SplashScreen(tk_root, hold_ms=5000)
    try:
        script = str(screen.card.tk.call("bind", str(screen.card), "<Escape>"))
        assert script, "the card does not listen for Escape"
    finally:
        screen.finish()



# --------------------------------------------------------------------------- #
# 12. Documented limits drifted from the enforced ones
# --------------------------------------------------------------------------- #
@pytest.mark.regression
@pytest.mark.unit
def test_readme_limits_match_the_enforced_caps() -> None:
    """The documented limits are the ones users rely on; keep them honest.

    Each cap is a real behaviour change -- a file that used to convert may now
    be truncated or refused -- so the README has to state the number that is
    actually enforced. A silent drift here is worse than no documentation.
    """

    from src.engine import office, pdf_converters

    readme = (_repo_root() / "README.md").read_text(encoding="utf-8")

    assert f"| {office._MAX_SHEET_ROWS:,} |" in readme, (
        f"README does not state the row cap ({office._MAX_SHEET_ROWS:,})"
    )
    assert f"| {office._MAX_SHEET_COLUMNS} |" in readme, (
        f"README does not state the column cap ({office._MAX_SHEET_COLUMNS})"
    )
    assert f"| {office._MAX_SHEET_XML_BYTES // (1024 * 1024)} MB |" in readme, (
        "README does not state the XML part cap"
    )
    assert f"| {pdf_converters._MAX_TOTAL_PIXELS // 1_000_000} M" in readme, (
        "README does not state the image pixel budget"
    )


@pytest.mark.regression
@pytest.mark.integration
def test_truncation_is_always_reported_not_silent(
    scratch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A truncated output looks complete, so the note must never be optional.

    The cap converts rather than refuses, which means a partial file can look
    perfectly normal in any other tool. Both affected converters therefore take
    ``notes`` and both write the note into the document itself.

    Asserted by behaviour, not by reading the source: the note has to appear in
    the output even when the caller passes *no* ``notes`` list at all. That was
    the real defect -- the in-document warning used to be gated on a caller
    remembering that argument, so a caller that forgot it silently produced a
    truncated file with no warning anywhere. A previous version of this test
    matched the literal string ``"if notes:"`` in the source, which would have
    passed while that behaviour was broken.

    The cap is lowered rather than building a 10,000-row sheet, so the test
    stays fast.
    """

    import inspect

    from src.engine import office
    from tests.support import builders

    for converter in (office.excel_to_pdf, office.excel_to_word):
        signature = inspect.signature(converter)
        assert "notes" in signature.parameters, (
            f"{converter.__name__} cannot be told that it truncated"
        )
        assert signature.parameters["notes"].kind is inspect.Parameter.KEYWORD_ONLY

    monkeypatch.setattr(office, "_MAX_SHEET_ROWS", 5)

    source = builders.make_xlsx(scratch / "wide.xlsx", big_rows=40, big_columns=3)

    # No ``notes`` argument at all -- the case that used to warn nobody.
    pdf_path = office.excel_to_pdf(source, scratch / "silent.pdf")
    pdf_text = builders.extract_all_text(pdf_path)
    assert "Note:" in pdf_text, (
        "excel_to_pdf produced a truncated PDF with no warning inside it"
    )

    import docx

    docx_path = office.excel_to_word(source, scratch / "silent.docx")
    word = docx.Document(str(docx_path))
    word_text = "\n".join(p.text for p in word.paragraphs)
    assert "Note:" in word_text, (
        "excel_to_word produced a truncated document with no warning inside it"
    )

    # And a caller that does pass a list is told too, for its own status line.
    notes: list = []
    office.excel_to_pdf(source, scratch / "reported.pdf", notes=notes)
    assert notes, "the UI banner would show nothing"


# --------------------------------------------------------------------------- #
# Build ordering
# --------------------------------------------------------------------------- #
def test_the_app_is_signed_before_the_installer_is_compiled() -> None:
    """ISCC copies dist/YourDoc into the setup at compile time.

    Signing the application after that compiles a signature onto a file the
    installer never contains, so everyone who installed got the unsigned copy and
    Defender flagged it. The app must be signed first, then wrapped.
"""

    source = (_repo_root() / "build_installer.py").read_text(encoding="utf-8")

    # Look only inside main(); the function definitions above it also contain
    # these names, and comparing against those would compare unrelated lines.
    main_body = source[source.index("def main("):]

    app_sign = main_body.index("sign_app(")
    compile_step = main_body.index("run_iscc(")
    setup_sign = main_body.index("sign_setup(")

    assert app_sign < compile_step, (
        "the app is signed after the installer is compiled, so the signature "
        "never reaches the installed copy"
    )
    assert compile_step < setup_sign, "the installer must be signed after compiling"


def test_the_installed_payload_carries_the_signature(tmp_path) -> None:
    """The frozen exe that ships must itself be signed, not just the setup.

    Reading the auth directory directly avoids needing a certificate store, so
    this runs on a machine that has not trusted the Pryonx root.
    """

    exe = _repo_root() / "dist" / "YourDoc" / "YourDoc.exe"
    if not exe.is_file():
        pytest.skip("no frozen build; run `python build_installer.py` first")

    # WIN_CERT_TYPE_PKCS_SIGNED_DATA, described by the signed blob's length in
    # the trailing certificate table.
    data = exe.read_bytes()
    assert b"\x30\x82" in data[-8192:], "no signature block at the end of the file"


# --------------------------------------------------------------------------- #
# The installer guidance must not promise what it cannot deliver
# --------------------------------------------------------------------------- #
def test_the_installer_instructions_are_honest_about_smartscreen() -> None:
    """The shipped guidance must not claim the certificate removes the warning.

    A user installed YourDoc, saw the SmartScreen warning, and asked why -- with
    good cause, because the guidance implied the certificate was supposed to make
    it go away. Microsoft treats a self-signed certificate as though the file
    were unsigned, so the warning appears regardless. Saying otherwise sends
    people looking for a fault that does not exist.
    """

    text = (_repo_root() / "installer" / "TRUSTING.md").read_text(encoding="utf-8")

    # It must lead with the thing that actually works: run it from a local copy.
    assert "Copy the files to a folder" in text, (
        "the guidance must open with the only step that reliably avoids the warning"
    )
    assert "More info" in text and "Run anyway" in text, (
        "the guidance must give the click-through steps"
    )

    # And it must be explicit that the certificate does not silence SmartScreen,
    # because that is the misleading part.
    assert "still shown" in text, (
        "the guidance must state plainly that SmartScreen warns even after the "
        "certificate is installed"
    )
    assert "separate" in text, (
        "the guidance must explain that the publisher bar and SmartScreen are "
        "two different checks"
    )


def test_the_installer_guidance_never_promises_a_warning_free_install() -> None:
    """No sentence may invite the user to expect silence from SmartScreen."""

    text = (_repo_root() / "installer" / "TRUSTING.md").read_text(encoding="utf-8").lower()

    for phrase in (
        "the warning will disappear",
        "the warning will not appear",
        "will no longer show",
        "prevents the warning",
        "you will not see a warning",
    ):
        assert phrase not in text, f"{phrase!r} is a promise Windows does not keep"


def test_the_installer_guidance_no_longer_claims_a_source_requirement() -> None:
    """The AGPL explanation must be gone, and the commercial model stated.

    TRUSTING.md once told recipients to keep a source zip because AGPL section 6
    required it. YourDoc is now commercial, per-machine licensed software that
    needs a licence key, so the guide must say so plainly -- and must not still
    describe the product as MIT/free software -- while keeping support reachable.
    """

    text = (_repo_root() / "installer" / "TRUSTING.md").read_text(encoding="utf-8")
    assert "source zip" not in text.lower()
    assert "-source.zip" not in text
    assert "Affero" not in text
    assert "licence key" in text.lower(), "the key gate should be stated plainly"
    assert "proprietary" in text.lower(), "the commercial model must be named"
    assert "info.pryonx@gmail.com" in text


@pytest.mark.regression
def test_the_trust_guide_names_the_current_certificate() -> None:
    """TRUSTING.md must carry a fingerprint for the shipped certificate.

    Rotating the signing certificate leaves the old one trusted on every
    machine that installed it, and the symptom is `Get-AuthenticodeSignature`
    stubbornly reporting `UnknownError` with nothing in the docs to explain
    why. Publishing the fingerprint is what makes that diagnosable: without it
    a reader cannot tell which certificate they are supposed to trust.

    A plain shape check rather than a comparison against the real ``.pfx``,
    which lives outside the repository on purpose -- the certificate must not be
    a machine-dependent fact for the test suite.
    """

    import re

    text = (_repo_root() / "installer" / "TRUSTING.md").read_text(encoding="utf-8")
    fingerprints = set(re.findall(r"\b[0-9A-F]{64}\b", text))
    assert fingerprints, (
        "TRUSTING.md publishes no SHA-256 fingerprint for the signing "
        "certificate; readers cannot tell which certificate to trust"
    )

    # The replacement path must be documented too, not just the happy path.
    lowered = text.lower()
    assert "sha-1" in lowered or "sha1" in lowered, "no SHA-1 for the certmgr view"
    assert "certmgr.msc" in lowered, (
        "no instructions for removing a superseded certificate"
    )
# --------------------------------------------------------------------------- #
# Licence-file integrity: wrong licence claims and stray URLs must not ship
# --------------------------------------------------------------------------- #
@pytest.mark.regression
def test_no_shipped_licence_file_still_claims_agpl() -> None:
    """No user-facing file may still assert YourDoc is AGPL.

    The dependency that forced AGPL is gone, so any surviving claim is simply
    wrong -- and a wrong licence claim on a shipped document is a legal
    problem, not a cosmetic one.
    """

    for fname in (
        'THIRD_PARTY_NOTICES.md',
        'NOTICE',
        'README.md',
        'LICENSE',
        'installer/TRUSTING.md',
    ):
        text = (_repo_root() / fname).read_text(encoding='utf-8')
        for phrase in (
            'YourDoc is licensed under the GNU Affero',
            'YourDoc is free software, licensed under the **GNU Affero',
            'so the combined work is\noffered under the AGPL',
        ):
            assert phrase not in text, f'{fname} still claims AGPL: {phrase!r}'


@pytest.mark.regression
def test_notice_claims_no_false_repository() -> None:
    """NOTICE must not claim a public repository exists."""

    text = (_repo_root() / 'NOTICE').read_text(encoding='utf-8')
    assert 'published alongside the releases at the project repository' not in text
    assert 'every release is tagged' not in text


@pytest.mark.regression
def test_no_shipped_file_contains_pryonx_urls_except_vercel() -> None:
    """Only pryonx.vercel.app may appear in user-facing files."""

    allowed = {'pryonx.vercel.app', 'info.pryonx@gmail.com'}
    for fname in ('README.md', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'LICENSE',
                  'installer/TRUSTING.md', 'installer/yourdoc.iss'):
        text = (_repo_root() / fname).read_text(encoding='utf-8')
        # Find all URLs
        import re
        urls = re.findall(r'https?://[^\s\)]+', text)
        for url in urls:
            # Extract host
            host = url.split('/')[2].lower() if '://' in url else ''
            if host and host not in allowed:
                # Allow GNU in LICENSE
                if fname == 'LICENSE' and ('gnu.org' in url or 'fsf.org' in url):
                    continue
                # Allow timestamp.digicert.com in build_installer.py
                if fname == 'build_installer.py' and 'timestamp.digicert.com' in url:
                    continue
                assert False, f'{fname} contains disallowed URL: {url}'

# --------------------------------------------------------------------------- #
# Notices-table completeness: every pinned dependency must be attributed
# --------------------------------------------------------------------------- #
@pytest.mark.regression
def test_every_pinned_dependency_is_listed_in_the_notices() -> None:
    """A bundled-but-unlisted package is an attribution gap.

    pypdf shipped inside the frozen app for several releases while neither
    shipped document named it -- and BSD-3-Clause requires the copyright
    notice on redistribution. The table is the single place recipients look,
    so every pin in requirements.txt must appear there with its version.
    """

    import re

    root = _repo_root()
    pins: dict[str, str] = {}
    for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.-]+)", line or "")
        if match:
            pins[match.group(1).lower()] = match.group(2)

    assert pins, "no pins parsed from requirements.txt"
    notices = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    lowered = notices.lower()

    for name, version in sorted(pins.items()):
        assert name in lowered, (
            f"{name} is pinned in requirements.txt but missing from "
            "THIRD_PARTY_NOTICES.md"
        )
        assert version in notices, (
            f"{name}=={version} is pinned, but version {version} is not in "
            "THIRD_PARTY_NOTICES.md -- the table is stale"
        )
