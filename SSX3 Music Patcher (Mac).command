#!/bin/sh
# ===========================================================
#  SSX 3 Custom Music Patcher  --  macOS
#  Double-click this file. Windows users: use the .cmd file.
#
#  If macOS refuses to run it ("cannot be opened because it is
#  from an unidentified developer"), either right-click -> Open,
#  or run this once in Terminal:
#      chmod +x "SSX3 Music Patcher (Mac).command"
# ===========================================================
cd "$(dirname "$0")" || exit 1
for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1; then
    exec "$py" ssx3_music_gui.py
  fi
done
echo "Python 3 was not found."
echo "Install it with:  brew install python-tk"
echo "(or from python.org - the python.org build includes tkinter)"
echo
echo "Press return to close."
read -r _
