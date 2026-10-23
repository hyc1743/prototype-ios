# SPDX-License-Identifier: GPL-3.0-or-later
# Adapted from fungalspores/prototype-switch prototype/CMakeLists.txt.
cmake_minimum_required(VERSION 3.25)
project(PrototypeApple VERSION 0.1.0 LANGUAGES C CXX OBJC OBJCXX ASM)

if(NOT APPLE)
    message(FATAL_ERROR "PrototypeApple requires AppleClang and an Apple SDK")
endif()
if(NOT CMAKE_OSX_ARCHITECTURES STREQUAL "arm64")
    message(FATAL_ERROR "Configure with -DCMAKE_OSX_ARCHITECTURES=arm64")
endif()
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(PROTOTYPE_SOURCE_ROOT "${CMAKE_CURRENT_LIST_DIR}/prototype" CACHE PATH "Prepared original app plus generated code")
set(REXSDK_DIR "${CMAKE_CURRENT_LIST_DIR}/sdk" CACHE PATH "Prepared pinned SDK with reviewed overlays")
set(MOLTENVK_ROOT "$ENV{MOLTENVK_ROOT}" CACHE PATH "Pinned MoltenVK root containing include and static")
set(PROTOTYPE_APPLE_ENTRY_SOURCE "" CACHE FILEPATH "Optional real UIKit/SDL application entry wrapper")
set(PROTOTYPE_EXTRA_SOURCES "" CACHE STRING "Additional importer/controller Objective-C or C++ sources")
set(PROTOTYPE_EXTRA_INCLUDE_DIRS "" CACHE STRING "Headers for the application importer and controller")
set(PROTOTYPE_INFO_PLIST "${CMAKE_CURRENT_LIST_DIR}/Info.plist.in" CACHE FILEPATH "Reviewed iOS bundle plist template")
if(NOT EXISTS "${PROTOTYPE_SOURCE_ROOT}/generated/default/sources.cmake" OR
   NOT EXISTS "${REXSDK_DIR}/src/system/CMakeLists.txt")
    message(FATAL_ERROR "Run tools/prepare_source_port.py with actual generated/default first")
endif()

set(REXGLUE_PLATFORM_SWITCH OFF CACHE BOOL "" FORCE)
set(REXGLUE_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(REXGLUE_STATIC_RUNTIME ON CACHE BOOL "" FORCE)
set(REXGLUE_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(REXGLUE_ENABLE_TRACY OFF CACHE BOOL "" FORCE)
set(REXGLUE_ENABLE_PERF_COUNTERS OFF CACHE BOOL "" FORCE)
set(REXGLUE_ENABLE_FIDELITYFX OFF CACHE BOOL "" FORCE)
set(REXGLUE_USE_D3D12 OFF CACHE BOOL "" FORCE)
set(REXGLUE_USE_VULKAN ON CACHE BOOL "" FORCE)
set(REXGLUE_UPSCALE_SHADERS ON CACHE BOOL "" FORCE)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
add_compile_definitions(REXGLUE_STATIC_RUNTIME=1 REX_HAS_FIDELITYFX_SDK=1)

if(CMAKE_SYSTEM_NAME STREQUAL "iOS")
    set(REXGLUE_PLATFORM_IOS ON)
    set(_prototype_mvk_slice "ios-arm64")
    add_compile_definitions(REX_PLATFORM_IOS=1)
else()
    set(_prototype_mvk_slice "macos-arm64_x86_64")
endif()
set(_prototype_mvk_library "${MOLTENVK_ROOT}/static/MoltenVK.xcframework/${_prototype_mvk_slice}/libMoltenVK.a")
if(NOT EXISTS "${_prototype_mvk_library}" OR NOT EXISTS "${MOLTENVK_ROOT}/include/vulkan/vulkan.h")
    message(FATAL_ERROR "MOLTENVK_ROOT must contain the pinned static ${_prototype_mvk_slice} library and headers")
endif()
add_library(prototype_moltenvk STATIC IMPORTED GLOBAL)
set_target_properties(prototype_moltenvk PROPERTIES IMPORTED_LOCATION "${_prototype_mvk_library}")
add_subdirectory("${REXSDK_DIR}" rexglue-sdk)

set(GEN "${PROTOTYPE_SOURCE_ROOT}/generated/default")
include("${GEN}/sources.cmake")
add_library(prototype_recomp OBJECT ${GENERATED_SOURCES})
target_include_directories(prototype_recomp PRIVATE "${PROTOTYPE_SOURCE_ROOT}" "${PROTOTYPE_SOURCE_ROOT}/src" "${GEN}")
target_link_libraries(prototype_recomp PRIVATE rex::runtime)
target_precompile_headers(prototype_recomp PRIVATE "${GEN}/prototype_pch.h")
target_compile_options(prototype_recomp PRIVATE -fno-slp-vectorize -fno-vectorize -ffp-contract=off -fno-strict-aliasing)
set_source_files_properties("${GEN}/prototype_register.cpp" PROPERTIES COMPILE_OPTIONS "-O0" SKIP_PRECOMPILE_HEADERS ON)

file(GLOB PROTOTYPE_APP_SOURCES CONFIGURE_DEPENDS
    "${PROTOTYPE_SOURCE_ROOT}/src/*.cpp" "${PROTOTYPE_SOURCE_ROOT}/src/native/*.cpp")
if(CMAKE_SYSTEM_NAME STREQUAL "iOS")
    if(NOT EXISTS "${PROTOTYPE_INFO_PLIST}")
        message(FATAL_ERROR "Set PROTOTYPE_INFO_PLIST to the reviewed iOS Info.plist template")
    endif()
    add_executable(prototype MACOSX_BUNDLE ${PROTOTYPE_APP_SOURCES})
    set_target_properties(prototype PROPERTIES
        MACOSX_BUNDLE_INFO_PLIST "${PROTOTYPE_INFO_PLIST}"
        MACOSX_BUNDLE_GUI_IDENTIFIER "io.github.hyc1743.prototype"
        MACOSX_BUNDLE_BUNDLE_NAME "Prototype"
        MACOSX_BUNDLE_SHORT_VERSION_STRING "0.1.0"
        MACOSX_BUNDLE_BUNDLE_VERSION "1"
        XCODE_ATTRIBUTE_TARGETED_DEVICE_FAMILY "1,2"
        XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED "NO")
else()
    add_executable(prototype ${PROTOTYPE_APP_SOURCES})
endif()
target_sources(prototype PRIVATE "${REXSDK_DIR}/src/ui/windowed_app_main_sdl.cpp" "${REXSDK_DIR}/src/ui/rex_app.cpp")
if(PROTOTYPE_APPLE_ENTRY_SOURCE)
    if(NOT EXISTS "${PROTOTYPE_APPLE_ENTRY_SOURCE}")
        message(FATAL_ERROR "PROTOTYPE_APPLE_ENTRY_SOURCE does not exist")
    endif()
    target_sources(prototype PRIVATE "${PROTOTYPE_APPLE_ENTRY_SOURCE}")
    # The wrapper owns UIApplication/SDL startup; its .mm translation unit can
    # call the C++ function int prototype_game_main(int, char **).
    set_source_files_properties("${REXSDK_DIR}/src/ui/windowed_app_main_sdl.cpp" PROPERTIES
        COMPILE_DEFINITIONS "SDL_MAIN_HANDLED=1;main=prototype_game_main")
endif()
if(PROTOTYPE_EXTRA_SOURCES)
    target_sources(prototype PRIVATE ${PROTOTYPE_EXTRA_SOURCES})
endif()
target_include_directories(prototype PRIVATE "${PROTOTYPE_SOURCE_ROOT}" "${PROTOTYPE_SOURCE_ROOT}/src" "${GEN}"
    "${REXSDK_DIR}/src/core" "${REXSDK_DIR}/src/graphics" "${REXSDK_DIR}/thirdparty/glslang"
    $<TARGET_PROPERTY:imgui,INTERFACE_INCLUDE_DIRECTORIES>)
if(PROTOTYPE_EXTRA_INCLUDE_DIRS)
    target_include_directories(prototype PRIVATE ${PROTOTYPE_EXTRA_INCLUDE_DIRS})
endif()
target_compile_options(prototype PRIVATE -ffp-contract=off -fno-strict-aliasing)
target_compile_options(prototype PRIVATE "$<$<COMPILE_LANGUAGE:OBJC,OBJCXX>:-fobjc-arc>")
# The runtime already owns rexcore/rexui/etc.; linking those object libraries a
# second time would duplicate the cvar registry and other process state.
target_link_libraries(prototype PRIVATE prototype_recomp rex::runtime rexgpu-xenos prototype_moltenvk
    "-framework Metal" "-framework QuartzCore" "-framework IOSurface" "-framework Foundation")
# Runtime archives contain registration constructors and overrides of weak
# generated imports. Keep those members as the original static Switch build
# does, using ld64's archive option rather than ELF --whole-archive.
target_link_options(prototype PRIVATE
    "LINKER:-force_load,$<TARGET_FILE:rexruntime>"
    "LINKER:-force_load,$<TARGET_FILE:rexgpu-xenos>")
set_property(TARGET prototype APPEND PROPERTY LINK_DEPENDS
    "$<TARGET_FILE:rexruntime>" "$<TARGET_FILE:rexgpu-xenos>")
if(CMAKE_SYSTEM_NAME STREQUAL "iOS")
    target_link_libraries(prototype PRIVATE "-framework UIKit" "-framework AVFoundation" "-framework GameController")
else()
    target_link_libraries(prototype PRIVATE "-framework AppKit" "-framework IOKit" "-framework CoreGraphics")
endif()
if(CMAKE_SYSTEM_NAME STREQUAL "iOS")
    set_target_properties(prototype PROPERTIES OUTPUT_NAME Prototype)
else()
    set_target_properties(prototype PROPERTIES OUTPUT_NAME PrototypeNative)
endif()
add_custom_command(TARGET prototype POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different "${PROTOTYPE_SOURCE_ROOT}/prototype.toml" "$<TARGET_FILE_DIR:prototype>/prototype.toml"
    VERBATIM)
