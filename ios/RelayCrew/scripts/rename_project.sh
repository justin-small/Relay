#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PARENT_DIR="$(cd "$ROOT_DIR/.." && pwd)"
ROOT_BASENAME="$(basename "$ROOT_DIR")"

ENV_FILE="$ROOT_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

if [[ $# -ge 2 ]]; then
  NEW_NAME="$1"
  NEW_BUNDLE_ID="$2"
else
  NEW_NAME="${NEW_PROJECT_NAME:-}"
  NEW_BUNDLE_ID="${NEW_BUNDLE_ID:-}"
fi

if [[ -z "${NEW_NAME:-}" || -z "${NEW_BUNDLE_ID:-}" ]]; then
  echo "Usage: $0 <NewProjectName> <NewBundleID>"
  echo "Or set NEW_PROJECT_NAME and NEW_BUNDLE_ID in .env"
  exit 1
fi

PROJECT_FILE="$(ls "$ROOT_DIR"/*.xcodeproj 2>/dev/null | head -n 1 || true)"
if [[ -z "$PROJECT_FILE" || ! "$PROJECT_FILE" =~ \.xcodeproj$ ]]; then
  # Fallback: locate project.pbxproj and infer xcodeproj folder
  PBXPROJ_PATH="$(find "$ROOT_DIR" -maxdepth 3 -name project.pbxproj | head -n 1 || true)"
  if [[ -z "$PBXPROJ_PATH" ]]; then
    echo "Error: No .xcodeproj or project.pbxproj found in $ROOT_DIR"
    exit 1
  fi
  PROJECT_FILE="$(cd "$(dirname "$PBXPROJ_PATH")" && pwd)"
fi

OLD_PROJECT_NAME="$(basename "$PROJECT_FILE" .xcodeproj)"
PBXPROJ="$PROJECT_FILE/project.pbxproj"

if [[ ! -f "$PBXPROJ" ]]; then
  echo "Error: Missing project.pbxproj at $PBXPROJ"
  exit 1
fi

OLD_MODULE="${OLD_PROJECT_NAME//-/_}"
NEW_MODULE="${NEW_NAME//-/_}"

# Detect current app source folder by presence of Config/Dev/Dev.xcconfig
APP_DIR="$(find "$ROOT_DIR" -maxdepth 3 -path "*/Config/Dev/Dev.xcconfig" -print | head -n 1 || true)"
if [[ -n "$APP_DIR" ]]; then
  APP_DIR="$(cd "$(dirname "$APP_DIR")/../.." && pwd)"
fi

if [[ -z "$APP_DIR" ]]; then
  APP_DIR="$ROOT_DIR/$OLD_PROJECT_NAME"
fi

if [[ ! -d "$APP_DIR" ]]; then
  echo "Error: Could not find app source folder."
  exit 1
fi

APP_DIR_NAME="$(basename "$APP_DIR")"

export ROOT_DIR
export PBXPROJ
export OLD_PROJECT_NAME
export APP_DIR
export APP_DIR_NAME
export NEW_NAME
export NEW_BUNDLE_ID
export OLD_MODULE
export NEW_MODULE

python3 - <<'PY'
import os, re

pbxproj = os.environ["PBXPROJ"]
old_name = os.environ["OLD_PROJECT_NAME"]
new_name = os.environ["NEW_NAME"]
old_module = os.environ["OLD_MODULE"]
new_module = os.environ["NEW_MODULE"]
new_bundle_id = os.environ["NEW_BUNDLE_ID"]

with open(pbxproj, "r", encoding="utf-8") as f:
    data = f.read()

# Replace project/target names
data = data.replace(old_name, new_name)

# Replace module name if present
data = data.replace(old_module, new_module)

# Helper: update bundle id for specific target build configurations
def update_bundle_id_for_target(data, target_name, bundle_id):
    # Find configuration list for target
    pattern = rf"([A-F0-9]{{24}}) /\* Build configuration list for PBXNativeTarget \"{re.escape(target_name)}\" \*/ = \{{.*?buildConfigurations = \((.*?)\);.*?\}};"
    m = re.search(pattern, data, re.S)
    if not m:
        return data
    config_list = m.group(2)
    config_ids = re.findall(r"([A-F0-9]{24}) /\* .*? \*/", config_list)
    for cid in config_ids:
        block_pattern = rf"({cid} /\* .*? \*/ = \{{.*?buildSettings = \{{)(.*?)(\}}\;.*?\n\t\t\}}\;)"
        bm = re.search(block_pattern, data, re.S)
        if not bm:
            continue
        block = bm.group(0)
        if "PRODUCT_BUNDLE_IDENTIFIER" in block:
            block_new = re.sub(
                r"PRODUCT_BUNDLE_IDENTIFIER = [^;]+;",
                f'PRODUCT_BUNDLE_IDENTIFIER = "{bundle_id}";',
                block
            )
        else:
            # Insert before end of buildSettings
            block_new = block.replace("buildSettings = {\n", f"buildSettings = {{\n\t\t\t\tPRODUCT_BUNDLE_IDENTIFIER = \"{bundle_id}\";\n")
        data = data.replace(block, block_new)
    return data

def get_team_for_target(data, target_name):
    pattern = rf"([A-F0-9]{{24}}) /\* Build configuration list for PBXNativeTarget \"{re.escape(target_name)}\" \*/ = \{{.*?buildConfigurations = \((.*?)\);.*?\}};"
    m = re.search(pattern, data, re.S)
    if not m:
        return None
    config_list = m.group(2)
    config_ids = re.findall(r"([A-F0-9]{24}) /\* .*? \*/", config_list)
    for cid in config_ids:
        block_pattern = rf"{cid} /\* .*? \*/ = \{{.*?buildSettings = \{{(.*?)\}}\;.*?\n\t\t\}}\;"
        bm = re.search(block_pattern, data, re.S)
        if not bm:
            continue
        settings = bm.group(1)
        tm = re.search(r"DEVELOPMENT_TEAM = ([^;]+);", settings)
        if tm:
            return tm.group(1).strip().strip('"')
    return None

def update_setting_for_target(data, target_name, key, value):
    pattern = rf"([A-F0-9]{{24}}) /\* Build configuration list for PBXNativeTarget \"{re.escape(target_name)}\" \*/ = \{{.*?buildConfigurations = \((.*?)\);.*?\}};"
    m = re.search(pattern, data, re.S)
    if not m:
        return data
    config_list = m.group(2)
    config_ids = re.findall(r"([A-F0-9]{24}) /\* .*? \*/", config_list)
    for cid in config_ids:
        block_pattern = rf"({cid} /\* .*? \*/ = \{{.*?buildSettings = \{{)(.*?)(\}}\;.*?\n\t\t\}}\;)"
        bm = re.search(block_pattern, data, re.S)
        if not bm:
            continue
        block = bm.group(0)
        if key in block:
            block_new = re.sub(
                rf"{re.escape(key)} = [^;]+;",
                f'{key} = {value};',
                block
            )
        else:
            block_new = block.replace("buildSettings = {\n", f"buildSettings = {{\n\t\t\t\t{key} = {value};\n")
        data = data.replace(block, block_new)
    return data

# Update app/test/uitest bundle ids without making them identical.
# The app target reads its id from the per-env .xcconfig (BUNDLE_ID), so keep the
# variable here — the .xcconfig values below give Dev/Staging/Prod distinct ids.
data = update_bundle_id_for_target(data, new_name, "$(BUNDLE_ID)")
data = update_bundle_id_for_target(data, f"{new_name}Tests", f"{new_bundle_id}.tests")
data = update_bundle_id_for_target(data, f"{new_name}UITests", f"{new_bundle_id}.uitests")

# Sync DEVELOPMENT_TEAM from main target to tests/uitests
team = get_team_for_target(data, new_name)
if team:
    data = update_setting_for_target(data, f"{new_name}Tests", "DEVELOPMENT_TEAM", team)
    data = update_setting_for_target(data, f"{new_name}UITests", "DEVELOPMENT_TEAM", team)

with open(pbxproj, "w", encoding="utf-8") as f:
    f.write(data)
PY

# Rename main source folder
if [[ "$APP_DIR_NAME" != "$NEW_NAME" ]]; then
  mv "$APP_DIR" "$ROOT_DIR/$NEW_NAME"
fi

# Rename tests folders if present
if [[ -d "$ROOT_DIR/${APP_DIR_NAME}Tests" ]]; then
  mv "$ROOT_DIR/${APP_DIR_NAME}Tests" "$ROOT_DIR/${NEW_NAME}Tests"
fi
if [[ -d "$ROOT_DIR/${APP_DIR_NAME}UITests" ]]; then
  mv "$ROOT_DIR/${APP_DIR_NAME}UITests" "$ROOT_DIR/${NEW_NAME}UITests"
fi

# Rename default test file and update content
TEST_FILE_DIR="$ROOT_DIR/${NEW_NAME}Tests"
if [[ -d "$TEST_FILE_DIR" ]]; then
  NEW_TEST_FILE="$TEST_FILE_DIR/${NEW_MODULE}Tests.swift"

  # If the new file doesn't exist, rename the first *Tests.swift we find
  if [[ ! -f "$NEW_TEST_FILE" ]]; then
    OLD_TEST_FILE="$(find "$TEST_FILE_DIR" -maxdepth 1 -name "*Tests.swift" | head -n 1 || true)"
    if [[ -n "$OLD_TEST_FILE" && "$OLD_TEST_FILE" != "$NEW_TEST_FILE" ]]; then
      mv "$OLD_TEST_FILE" "$NEW_TEST_FILE"
    fi
  fi

  if [[ -f "$NEW_TEST_FILE" ]]; then
    sed -i '' "s/${OLD_MODULE}Tests/${NEW_MODULE}Tests/g" "$NEW_TEST_FILE" 2>/dev/null || true
    sed -i '' "s/${OLD_PROJECT_NAME}Tests/${NEW_NAME}Tests/g" "$NEW_TEST_FILE" 2>/dev/null || true
  fi
fi

# Rename xcodeproj
OLD_XCODEPROJ="$PROJECT_FILE"
NEW_XCODEPROJ="$ROOT_DIR/$NEW_NAME.xcodeproj"
if [[ "$OLD_XCODEPROJ" != "$NEW_XCODEPROJ" ]]; then
  mv "$OLD_XCODEPROJ" "$NEW_XCODEPROJ"
fi

# Remove old xcodeproj if still exists
if [[ -d "$ROOT_DIR/$OLD_PROJECT_NAME.xcodeproj" && "$OLD_PROJECT_NAME" != "$NEW_NAME" ]]; then
  rm -rf "$ROOT_DIR/$OLD_PROJECT_NAME.xcodeproj"
fi

# Update scheme names and contents
SCHEMES_DIR="$ROOT_DIR/$NEW_NAME.xcodeproj/xcshareddata/xcschemes"
if [[ -d "$SCHEMES_DIR" ]]; then
  for f in "$SCHEMES_DIR"/*.xcscheme; do
    [[ -f "$f" ]] || continue
    sed -i '' "s/${OLD_PROJECT_NAME}/${NEW_NAME}/g" "$f" 2>/dev/null || true
    base="$(basename "$f")"
    if [[ "$base" == *"$OLD_PROJECT_NAME"* ]]; then
      mv "$f" "$SCHEMES_DIR/${base//$OLD_PROJECT_NAME/$NEW_NAME}"
    fi
  done
fi

# General source rename across ALL Swift files — covers the @main app
# struct, @testable imports, type names, etc. (content + filenames).
find "$ROOT_DIR" -name "*.swift" -not -path "*/.build/*" -print0 | while IFS= read -r -d '' file; do
  sed -i '' \
    -e "s/${OLD_MODULE}/${NEW_MODULE}/g" \
    -e "s/${OLD_PROJECT_NAME}/${NEW_NAME}/g" \
    "$file" 2>/dev/null || true
done

# Rename Swift files whose name contains the old module/project name
# (e.g. <Old>App.swift -> <New>App.swift, <Old>Tests.swift -> <New>Tests.swift).
find "$ROOT_DIR" -name "*.swift" -not -path "*/.build/*" -print0 | while IFS= read -r -d '' file; do
  dir="$(dirname "$file")"
  base="$(basename "$file")"
  newbase="${base//${OLD_MODULE}/${NEW_MODULE}}"
  newbase="${newbase//${OLD_PROJECT_NAME}/${NEW_NAME}}"
  if [[ "$base" != "$newbase" ]]; then
    mv "$file" "$dir/$newbase"
  fi
done

echo "✅ Rename complete."
echo "Project: $OLD_PROJECT_NAME -> $NEW_NAME"
echo "Module:  $OLD_MODULE -> $NEW_MODULE"
echo "Bundle:  $NEW_BUNDLE_ID (app/tests/uitests)"

# Update xcconfig values (BUNDLE_ID / APP_NAME)
CONFIG_ROOT="$ROOT_DIR/$NEW_NAME/Config"
if [[ -d "$CONFIG_ROOT" ]]; then
  update_xcconfig() {
    local file="$1"
    local bundle="$2"
    local appname="$3"
    [[ -f "$file" ]] || return 0
    if grep -q "^BUNDLE_ID" "$file"; then
      sed -i '' "s/^BUNDLE_ID.*/BUNDLE_ID = ${bundle}/" "$file" 2>/dev/null || true
    else
      printf "\nBUNDLE_ID = %s\n" "$bundle" >> "$file"
    fi
    if grep -q "^APP_NAME" "$file"; then
      sed -i '' "s/^APP_NAME.*/APP_NAME = ${appname}/" "$file" 2>/dev/null || true
    else
      printf "APP_NAME = %s\n" "$appname" >> "$file"
    fi
  }

  update_xcconfig "$CONFIG_ROOT/Dev/Dev.xcconfig" "${NEW_BUNDLE_ID}.dev" "${NEW_NAME}-Dev"
  update_xcconfig "$CONFIG_ROOT/Staging/Staging.xcconfig" "${NEW_BUNDLE_ID}.staging" "${NEW_NAME}-Staging"
  update_xcconfig "$CONFIG_ROOT/Prod/Prod.xcconfig" "${NEW_BUNDLE_ID}.prod" "${NEW_NAME}"
fi

# Update AGENTS.md so the scheme names / project references in the commands an
# AI agent runs stay correct after the rename (the script otherwise only touches
# pbxproj, schemes, Swift, and xcconfig — not Markdown).
AGENTS_FILE="$ROOT_DIR/AGENTS.md"
if [[ -f "$AGENTS_FILE" ]]; then
  sed -i '' \
    -e "s/${OLD_MODULE}/${NEW_MODULE}/g" \
    -e "s/${OLD_PROJECT_NAME}/${NEW_NAME}/g" \
    "$AGENTS_FILE" 2>/dev/null || true
fi

# Rename root folder at the end (rename current root, not move into another root)
NEW_ROOT="$PARENT_DIR/$NEW_NAME"
if [[ "$ROOT_BASENAME" != "$NEW_NAME" ]]; then
  if [[ -e "$NEW_ROOT" ]]; then
    echo "⚠️  Target root folder already exists: $NEW_ROOT"
    echo "    Please move/rename it or choose another project name."
    exit 1
  fi
  mv "$ROOT_DIR" "$NEW_ROOT"
  echo "📁 Root folder renamed to: $NEW_ROOT"
  echo "➡️  Please open the project from the new folder path."
fi
