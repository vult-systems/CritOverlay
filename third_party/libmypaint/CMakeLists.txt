# Builds libmypaint as one self-contained mypaint.dll with MSVC: json-c is
# linked statically, glib and gettext are compiled out, OpenMP is off. Driven
# by build_libmypaint.py, which supplies LIBMYPAINT_SRC, JSONC_DIR and the
# generated config headers in CONFIG_DIR.
cmake_minimum_required(VERSION 3.20)
project(mypaint C)

set(CMAKE_WINDOWS_EXPORT_ALL_SYMBOLS ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")   # no VC++ runtime DLL to ship

find_package(json-c CONFIG REQUIRED PATHS "${JSONC_DIR}" NO_DEFAULT_PATH)

set(S "${LIBMYPAINT_SRC}")
add_library(mypaint SHARED
    ${S}/brushmodes.c ${S}/fifo.c ${S}/helpers.c
    ${S}/mypaint.c ${S}/mypaint-brush.c ${S}/mypaint-brush-settings.c
    ${S}/mypaint-fixed-tiled-surface.c ${S}/mypaint-mapping.c ${S}/mypaint-matrix.c
    ${S}/mypaint-rectangle.c ${S}/mypaint-surface.c ${S}/mypaint-symmetry.c
    ${S}/mypaint-tiled-surface.c ${S}/operationqueue.c ${S}/rng-double.c ${S}/tilemap.c
    ${CMAKE_CURRENT_SOURCE_DIR}/crit_surface.c)
target_include_directories(mypaint PRIVATE "${CONFIG_DIR}" "${S}")
target_compile_definitions(mypaint PRIVATE _USE_MATH_DEFINES _CRT_SECURE_NO_WARNINGS)
target_link_libraries(mypaint PRIVATE json-c::json-c)
if(MSVC)
    target_compile_options(mypaint PRIVATE /O2 /fp:fast /W1)
endif()
