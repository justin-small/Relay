# The setup network's resolver and its address. Header-only and free of
# ESP-IDF, so it has no REQUIRES. Both targets' `test_native.cpp` compile it with
# g++ on a laptop.
idf_component_register(
    INCLUDE_DIRS "include"
)
