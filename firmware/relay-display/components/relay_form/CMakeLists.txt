# The `application/x-www-form-urlencoded` parser behind every settings page
#. Header-only and free of ESP-IDF, so it has no REQUIRES. `test_native.cpp`
# compiles it with g++ on a laptop.
idf_component_register(
    INCLUDE_DIRS "include"
)
