# The Relay caption panel's firmware, for the Elecrow CrowPanel Advance 7.0". ESP-IDF, pinned in README.md.
#
# **The project is named `relay-display-app`, and the merged image is not.**
# `idf.py` names the application image after
# the project, and this build produces two files that must not be confused.
#
#   build/relay-display-app.bin   the application alone, the file an update
#                                       over the network would carry
#   build/relay-display.bin       bootloader, partition table, otadata and
#                                       the application, flashed at 0x0 in one go
#
# Naming the project for the merged file would give the application that name
# instead, and an installer who flashes the wrong one at 0x0 gets a panel that
# does not boot.
cmake_minimum_required(VERSION 3.16)

# **The version inside the image is `RELAY_FW_VERSION`.** ESP-IDF fills
# `esp_app_desc_t.version` from `git describe` unless told otherwise, which is a
# hash off a tag, `-dirty` in a working tree, and nothing at all in a tarball.
# The same read as the I/O board, so the number on the panel's boot line is the
# one in `config.h` and nothing else.
file(STRINGS "${CMAKE_CURRENT_LIST_DIR}/main/config.h" _relay_fw_version
     REGEX "^#define[ \t]+RELAY_FW_VERSION[ \t]+\"[^\"]*\"")
string(REGEX REPLACE ".*\"([^\"]*)\".*" "\\1" PROJECT_VER "${_relay_fw_version}")
if(NOT PROJECT_VER MATCHES "^[0-9]+\\.[0-9]+\\.[0-9]+$")
    message(FATAL_ERROR "main/config.h: RELAY_FW_VERSION is not X.Y.Z (got '${PROJECT_VER}')")
endif()

# This target's own copies of the header-only components (`relay_auth`, `relay_button`,
# `relay_captive`, `relay_form`). Each is listed as a REQUIRES in `main/CMakeLists.txt` only
# when the target calls it, so this line makes them findable and adds nothing to the image by itself. Must be set before
# `project.cmake` is included, which is where ESP-IDF scans for components.
set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/components")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(relay-display-app)
