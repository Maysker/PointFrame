#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="${1:-0.1.1}"
ARCH="amd64"
PACKAGE="pointframe"
ROOT="build/deb/${PACKAGE}_${VERSION}_${ARCH}"
OUTPUT="dist/${PACKAGE}_${VERSION}_${ARCH}.deb"

if [[ ! -x dist/PointFrame/PointFrame ]]; then
  echo "Standalone build not found. Run ./packaging/build-standalone.sh first."
  exit 1
fi

rm -rf "$ROOT"

mkdir -p \
  "$ROOT/DEBIAN" \
  "$ROOT/opt/pointframe" \
  "$ROOT/usr/bin" \
  "$ROOT/usr/share/applications" \
  "$ROOT/usr/share/icons/hicolor"

cp -a dist/PointFrame/. "$ROOT/opt/pointframe/"
cp -a assets/icons/hicolor/. "$ROOT/usr/share/icons/hicolor/"

cat > "$ROOT/usr/bin/pointframe" <<'EOF'
#!/usr/bin/env bash
exec /opt/pointframe/PointFrame "$@"
EOF
chmod 755 "$ROOT/usr/bin/pointframe"

cat > "$ROOT/usr/share/applications/io.github.Maysker.PointFrame.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=PointFrame
Comment=Point-cloud alignment and precision cropping tool
Exec=pointframe
Icon=pointframe
Terminal=false
Categories=Graphics;
StartupNotify=true
EOF

cat > "$ROOT/DEBIAN/control" <<EOF
Package: ${PACKAGE}
Version: ${VERSION}
Section: graphics
Priority: optional
Architecture: ${ARCH}
Maintainer: Adam Gazdiev
Homepage: https://github.com/Maysker/PointFrame
Depends: zenity, libgtk-3-0t64, libwebkit2gtk-4.1-0, gir1.2-gtk-3.0, gir1.2-webkit2-4.1
Description: Local-first point-cloud alignment and precision cropping tool
 PointFrame provides local viewing, alignment, 3D inspection,
 polygon and height selection, and full-resolution PLY export.
EOF

mkdir -p dist
dpkg-deb --build --root-owner-group "$ROOT" "$OUTPUT"

echo
echo "Built: $OUTPUT"
ls -lh "$OUTPUT"
