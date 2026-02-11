#!/bin/bash

SRC_DIR="."
DEST_DIR="./backup"

# Create destination directory if it doesn't exist
mkdir -p "$DEST_DIR"

# Find and copy all .mscz files except those already in backup
find "$SRC_DIR" -type f -name "*.mscz" ! -path "$DEST_DIR/*" -exec cp "{}" "$DEST_DIR/" \;

echo "All .mscz files (excluding $DEST_DIR) have been copied to $DEST_DIR"
