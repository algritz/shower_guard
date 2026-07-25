#!/usr/bin/env bash

set -euo pipefail

###############################################################################
# Configuration
###############################################################################

REPO_ROOT="/home/biersh/Documents/git/shower_guard"
HA_CONFIG="/home/biersh/Documents/automation/home_assistant"

echo "Deploying Shower Guard..."

###############################################################################
# Ensure directories exist
###############################################################################

mkdir -p "$HA_CONFIG/packages"
mkdir -p "$HA_CONFIG/scripts"
mkdir -p "$HA_CONFIG/automations"

###############################################################################
# Copy package
###############################################################################

cp \
    "$REPO_ROOT/src/packages/shower_guard.yaml" \
    "$HA_CONFIG/packages/"

###############################################################################
# Copy scripts
###############################################################################

cp \
    "$REPO_ROOT/src/scripts/shower_guard.yaml" \
    "$HA_CONFIG/scripts/"

###############################################################################
# Copy automations
###############################################################################

cp \
    "$REPO_ROOT/src/automations/shower_guard.yaml" \
    "$HA_CONFIG/automations/"

echo
echo "Deployment complete."
echo
echo "Next:"
echo " 1. Validate configuration"
echo " 2. Reload YAML or restart Home Assistant"
