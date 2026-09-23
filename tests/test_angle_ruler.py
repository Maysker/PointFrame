import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_shared_angle_ruler_wraps_and_tracks_drag() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable for the angle-ruler test")
    module = PROJECT_ROOT / "src" / "pointframe" / "crop_web" / "angle-ruler.js"
    script = f"""
const ruler=require({json.dumps(str(module))});
const azimuth={{min:-180,max:180,cyclic:true,pixelsPerDegree:4}};
const elevation={{min:-180,max:180,cyclic:true,pixelsPerDegree:4}};
console.log(JSON.stringify({{
  wrappedPositive:ruler.constrain(181,azimuth),
  wrappedNegative:ruler.constrain(-181,azimuth),
  cyclicDrag:ruler.dragValue(179,-8,azimuth),
  elevationPositiveWrap:ruler.dragValue(170,-80,elevation),
  elevationNegativeWrap:ruler.dragValue(-170,80,elevation),
  horizontalDrag:ruler.dragValue(20,40,azimuth),
  verticalDrag:ruler.dragValue(20,40,elevation),
}}));
"""
    state = json.loads(subprocess.run([node, "-e", script], text=True, capture_output=True, check=True).stdout)
    assert state == {
        "wrappedPositive": -179,
        "wrappedNegative": 179,
        "cyclicDrag": -179,
        "elevationPositiveWrap": -170,
        "elevationNegativeWrap": 170,
        "horizontalDrag": 10,
        "verticalDrag": 10,
    }


def test_inspect_angles_use_shared_rulers_without_range_inputs() -> None:
    web = PROJECT_ROOT / "src" / "pointframe" / "crop_web"
    html = (web / "index.html").read_text(encoding="utf-8")
    app = (web / "app.js").read_text(encoding="utf-8")
    assert 'id="azimuthRange"' not in html
    assert 'id="elevationRange"' not in html
    assert html.count('class="angleRuler') == 3
    assert 'id="rollRuler"' in html
    assert html.index("angle-ruler.js") < html.index("app.js")
    assert app.count("AngleRuler.create") == 3
    assert 'setPrecisionAngle("azimuth"' in app
    assert 'setPrecisionAngle("elevation"' in app
    assert 'setPrecisionAngle("roll"' in app
    assert 'orientation:"vertical",min:-180,max:180,cyclic:true' in app
    assert "Nav.precisionOrbitBasis" not in app


def test_inspect_debug_and_horizontal_rulers_share_responsive_layout() -> None:
    web = PROJECT_ROOT / "src" / "pointframe" / "crop_web"
    html = (web / "index.html").read_text(encoding="utf-8")
    css = (web / "style.css").read_text(encoding="utf-8")

    shared_layout = re.search(r'<div id="inspectTopOverlay">\s*<pre id="orbitDebug"[^>]*></pre>\s*<div id="precisionControls"', html)
    assert shared_layout
    overlay_rule = re.search(r"#inspectTopOverlay\{([^}]*)\}", css).group(1)
    precision_rule = re.search(r"#precisionControls\{([^}]*)\}", css).group(1)
    debug_rule = re.search(r"#orbitDebug\{([^}]*)\}", css).group(1)
    assert "flex-wrap:wrap" in overlay_rule
    assert "body.inspect #inspectTopOverlay{display:flex}" in css
    assert "flex:1 1 420px" in precision_rule
    assert "position:absolute" not in precision_rule
    assert "position:absolute" not in debug_rule


def test_precision_rotations_preserve_basis_and_track_full_elevation_orbit() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable for the precision-camera test")
    module = PROJECT_ROOT / "src" / "pointframe" / "crop_web" / "navigation.js"
    script = f"""
const nav=require({json.dumps(str(module))});
const radians=degrees=>degrees*Math.PI/180;
const degrees=angle=>angle*180/Math.PI;

const original={{
  right:[0.36,-0.48,0.8],
  up:[0.8,0.6,0],
  depth:[-0.48,0.64,0.6],
}};
const unchanged=nav.rotateBasisAround(original,[0,0,1],0);

const rolled=nav.rotateBasisAround(nav.FIXED_BASES.front,nav.FIXED_BASES.front.depth,radians(37));
const elevated=nav.rotateBasisAround(rolled,rolled.right,radians(28));

let basis=nav.precisionOrbitBasis({{azimuth:0,elevation:radians(80)}});
let angles={{azimuth:0,elevation:radians(80)}};
const elevations=[];
const stepDeltas=[];
for(let index=0;index<36;index++){{
  const previous=angles.elevation;
  basis=nav.rotateBasisAround(basis,basis.right,-radians(10));
  angles=nav.continuousOrbitFromBasis(basis,angles);
  elevations.push(degrees(angles.elevation));
  stepDeltas.push(degrees(nav.angleDelta(angles.elevation,previous)));
}}

console.log(JSON.stringify({{
  zeroIsSameObject:unchanged===original,
  unchanged,
  original,
  rolledRight:rolled.right,
  elevatedRight:elevated.right,
  stepDeltas,
  elevations,
  finalBasis:basis,
}}));
"""
    state = json.loads(subprocess.run([node, "-e", script], text=True, capture_output=True, check=True).stdout)

    assert state["zeroIsSameObject"] is True
    assert state["unchanged"] == state["original"]
    assert abs(state["rolledRight"][2]) > 0.1
    assert state["elevatedRight"] == pytest.approx(state["rolledRight"], abs=1e-12)
    assert state["stepDeltas"] == pytest.approx([10] * 36, abs=1e-9)
    assert state["elevations"][0] == pytest.approx(90)
    assert state["elevations"][1] == pytest.approx(100)
    assert state["elevations"][9] == pytest.approx(-180)
    assert state["elevations"][18] == pytest.approx(-90)
    assert state["elevations"][27] == pytest.approx(0)
    assert state["elevations"][35] == pytest.approx(80)


def test_roll_rotation_preserves_view_direction_and_zero_delta_is_exact_noop() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable for the roll-camera test")
    module = PROJECT_ROOT / "src" / "pointframe" / "crop_web" / "navigation.js"
    script = f"""
const nav=require({json.dumps(str(module))});
const radians=degrees=>degrees*Math.PI/180;
const base=nav.rotateBasisAround(nav.FIXED_BASES.front,nav.FIXED_BASES.front.depth,radians(23));
const zero=nav.rotateBasisAround(base,base.depth,0);
const rolled=nav.rotateBasisAround(base,base.depth,radians(37));
console.log(JSON.stringify({{
  zeroIsSameObject:zero===base,
  zero,
  base,
  baseDepth:base.depth,
  rolledDepth:rolled.depth,
  baseRoll:nav.rollFromBasis(base),
  rolledRoll:nav.rollFromBasis(rolled),
}}));
"""
    state = json.loads(subprocess.run([node, "-e", script], text=True, capture_output=True, check=True).stdout)

    assert state["zeroIsSameObject"] is True
    assert state["zero"] == state["base"]
    assert state["rolledDepth"] == pytest.approx(state["baseDepth"], abs=1e-12)
    roll_delta = state["rolledRoll"] - state["baseRoll"]
    assert roll_delta == pytest.approx(37 * 3.141592653589793 / 180, abs=1e-12)
