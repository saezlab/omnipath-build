#!/bin/sh
# Meilisearch startup script with auto-import on data version change
# This script checks if the data version has changed and imports the dump if needed

set -e

DUMP_FILE_MARKER='/data/dumps/.dump_file'
VERSION_FILE='/data/.data_version'
IMPORTED_VERSION_FILE='/meili_data/.imported_version'

# Check if dump file marker exists
if [ ! -f "$DUMP_FILE_MARKER" ]; then
    echo "ERROR: Dump file marker not found at $DUMP_FILE_MARKER"
    echo "Run 'make export' from the root directory first."
    exit 1
fi

DUMP_NAME=$(cat "$DUMP_FILE_MARKER")
DUMP_FILE="/data/dumps/$DUMP_NAME"

# Check if actual dump file exists
if [ ! -f "$DUMP_FILE" ]; then
    echo "ERROR: Dump file not found: $DUMP_FILE"
    exit 1
fi

echo "Using dump file: $DUMP_FILE"

# Check if we need to import (new data version or first run)
NEED_IMPORT=false

if [ ! -f "$IMPORTED_VERSION_FILE" ]; then
    echo "First run - will import dump"
    NEED_IMPORT=true
elif [ -f "$VERSION_FILE" ]; then
    CURRENT_VERSION=$(cat "$VERSION_FILE")
    IMPORTED_VERSION=$(cat "$IMPORTED_VERSION_FILE")
    if [ "$CURRENT_VERSION" != "$IMPORTED_VERSION" ]; then
        echo "Data version changed: $IMPORTED_VERSION -> $CURRENT_VERSION"
        NEED_IMPORT=true
    else
        echo "Data version unchanged: $CURRENT_VERSION"
    fi
fi

if [ "$NEED_IMPORT" = "true" ]; then
    echo "Importing dump..."
    
    # Remove existing database if present (needed for re-import)
    if [ -d "/meili_data/data.ms" ]; then
        echo "Removing existing database for fresh import..."
        rm -rf /meili_data/data.ms
    fi
    
    # Also remove stale version marker to ensure clean state
    rm -f "$IMPORTED_VERSION_FILE"
    
    # Run import synchronously and check exit status
    if meilisearch --import-dump "$DUMP_FILE"; then
        echo "Import completed successfully"
        # Only save version marker AFTER successful import
        if [ -f "$VERSION_FILE" ]; then
            cp "$VERSION_FILE" "$IMPORTED_VERSION_FILE"
        else
            echo "imported" > "$IMPORTED_VERSION_FILE"
        fi
    else
        echo "ERROR: Import failed!"
        exit 1
    fi
else
    echo "Starting meilisearch with existing data..."
    meilisearch
fi
