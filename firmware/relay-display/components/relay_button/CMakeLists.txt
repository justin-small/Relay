# The BOOT button's hold policy: when a press is a recovery and when it is a
# factory reset. Header-only and free of ESP-IDF, so it has
# no REQUIRES. Both targets' `test_native.cpp` compile it with g++ on a laptop.
idf_component_register(
    INCLUDE_DIRS "include"
)
