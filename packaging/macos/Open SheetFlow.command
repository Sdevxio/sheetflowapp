#!/bin/bash
# First open after download. Removes the "damaged" block, then starts SheetFlow.
cd "$(dirname "$0")"
if [[ ! -d "SheetFlow.app" ]]; then
  echo "Keep this file in the same folder as SheetFlow.app, then run it again."
  read -r -p "Press Return to close."
  exit 1
fi
xattr -cr "SheetFlow.app"
open "SheetFlow.app"
