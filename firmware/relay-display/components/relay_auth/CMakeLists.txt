# HTTP Basic and the Origin check on state-changing requests, and
# the throttle on wrong credentials.
#
# Header-only, and nothing in it touches ESP-IDF, so there is nothing to
# REQUIRE: the C library headers it includes are always present. `test_native.cpp`
# compiles it with g++ on a laptop, and each board's HTTP server includes it.
idf_component_register(
    INCLUDE_DIRS "include"
)
