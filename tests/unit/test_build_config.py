"""Guards on the PyInstaller build configuration.

These assert on ``build.py``'s module-level package lists rather than running a
freeze, so they stay fast. They exist because a mistake here does not fail a
unit test or a lint -- it fails the release build minutes in, with a PyInstaller
COLLECT error that names neither the package nor the reason.
"""

from __future__ import annotations

import build


def test_tkinterdnd2_is_excluded_from_the_freeze() -> None:
    """tkinterdnd2 must not be bundled, and must be explicitly excluded.

    ``tkdnd`` is compiled against Tcl 8, but CPython 3.14 bundles Tcl/Tk 9, so
    ``TkinterDnD.Tk()`` raises "Unable to load tkdnd library" on this
    interpreter -- in the bundle exactly as in development. Drag & drop is off
    either way, so the package earns its place nowhere. Worse, collecting it
    broke the build: its linux-arm64 ``libtkdnd2.9.3.so`` collided with a file
    of the same name during COLLECT and PyInstaller aborted.

    It is listed in ``EXCLUDES`` (not merely omitted from the collect lists)
    because the UI imports it inside a try/except; without the explicit exclude,
    PyInstaller's static analysis would pull it back in.
    """

    assert "tkinterdnd2" in build.EXCLUDES, (
        "tkinterdnd2 is imported in a try/except, so it must be excluded outright "
        "or PyInstaller re-bundles it and the freeze breaks on the tkdnd collision"
    )
    assert "tkinterdnd2" not in build.COLLECT_ALL
    assert "tkinterdnd2" not in build.HIDDEN_IMPORTS
    assert "tkinterdnd2" not in build.COLLECT_SUBMODULES


def test_the_collect_lists_do_not_overlap_the_excludes() -> None:
    """A package cannot be both collected and excluded; the outcome is undefined.

    Not hypothetical: tkinterdnd2 once sat in ``COLLECT_ALL`` while its data
    files were also added by hand, which is what produced the duplicate-path
    failure. Keeping the two lists disjoint makes that class of mistake fail
    here instead of in the release build.
    """

    overlaps = set(build.COLLECT_ALL) & set(build.EXCLUDES)
    assert not overlaps, f"both collected and excluded: {sorted(overlaps)}"


def test_agpl_packages_are_not_collected_or_hidden_imported() -> None:
    """The AGPL stack must stay out of the freeze.

    YourDoc was rebuilt on pypdf/pypdfium2 precisely to drop PyMuPDF and
    pdf2docx. A stray entry here would silently reintroduce the licence the
    whole rewrite was done to remove.
    """

    banned = ("pymupdf", "fitz", "pdf2docx", "cv2", "opencv")
    listed = list(build.COLLECT_ALL) + list(build.HIDDEN_IMPORTS)
    offenders = [name for name in listed if name.lower() in banned]
    assert not offenders, f"AGPL components in the build config: {offenders}"
