from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_viewer_help_dialog_contains_controls_formats_and_about() -> None:
    web = PROJECT_ROOT / "src" / "pointframe" / "crop_web"
    html = (web / "index.html").read_text(encoding="utf-8")
    app = (web / "app.js").read_text(encoding="utf-8")

    assert '<button id="helpButton">Info</button>' in html
    assert '<dialog id="helpDialog"' in html
    for heading in ("Controls", "Supported formats", "About PointFrame"):
        assert f">{heading}<" in html
    for text in (
        "Free orbit", "Set orbit pivot", "Rotate around vertical axis",
        "Rotate above/below the scene", "Rotate around the viewing axis / level the view",
        "Add polygon vertex", "Move vertex", "Insert vertex", "Delete vertex",
        "PLY", "XYZ coordinates required", "RGB optional", "normals optional",
        "Version 0.1.0", "Point-cloud alignment and precision cropping tool.",
        "Processing is performed locally on your computer.",
    ):
        assert text in html
    repository = "https://github.com/Maysker/PointFrame"
    assert f'href="{repository}"' in html
    assert f">{repository}<" in html
    assert "LAS" not in html
    assert "LAZ" not in html
    assert '$("helpButton").onclick=()=>helpDialog.showModal()' in app
    assert '$("helpClose").onclick=()=>helpDialog.close()' in app
