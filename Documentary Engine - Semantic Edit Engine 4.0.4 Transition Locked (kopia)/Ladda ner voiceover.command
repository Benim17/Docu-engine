#!/bin/bash
set -e
cd "$(dirname "$0")"
read -r -p "Klistra in direktadressen till WAV-filen: " URL
if [ -z "$URL" ]; then echo "Ingen adress angavs."; exit 1; fi
mkdir -p input
echo "Laddar ner voiceover..."
/usr/bin/curl -L --fail --retry 3 --connect-timeout 20 -A "Mozilla/5.0" "$URL" -o input/voiceover.wav
echo "Klar: input/voiceover.wav"
read -n 1 -s -r -p "Tryck valfri tangent för att stänga."
echo
