cmake_minimum_required(VERSION 3.25)
project(jev_default_open_native LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
option(JEV_ENABLE_EXPERIMENTAL_HOOK "Allow unverified desktop-wide injection" OFF)

if(NOT WIN32 OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
    message(FATAL_ERROR "jev-default-open native components require x64 Windows")
endif()

if(JEV_ENABLE_EXPERIMENTAL_HOOK AND (
        NOT MSVC OR
        NOT CMAKE_CXX_COMPILER_ID STREQUAL "MSVC" OR
        NOT CMAKE_SIZEOF_VOID_P EQUAL 8))
    message(FATAL_ERROR
        "Experimental Detours hook requires the Microsoft MSVC x64 toolchain; "
        "use an x64 Visual Studio developer shell and configure with -A x64")
endif()

set(JSON_HEADER "${CMAKE_BINARY_DIR}/_deps/json/nlohmann/json.hpp")
set(JSON_SHA256 "9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6")
file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/_deps/json/nlohmann")
set(JSON_ACTUAL_SHA256 "")
if(EXISTS "${JSON_HEADER}")
    file(SHA256 "${JSON_HEADER}" JSON_ACTUAL_SHA256)
endif()
if(NOT JSON_ACTUAL_SHA256 STREQUAL JSON_SHA256)
    file(DOWNLOAD
        "https://raw.githubusercontent.com/nlohmann/json/v3.11.3/single_include/nlohmann/json.hpp"
        "${JSON_HEADER}"
        EXPECTED_HASH "SHA256=${JSON_SHA256}"
        TLS_VERIFY ON TIMEOUT 60 STATUS JSON_DOWNLOAD_STATUS)
    list(GET JSON_DOWNLOAD_STATUS 0 JSON_DOWNLOAD_CODE)
    if(NOT JSON_DOWNLOAD_CODE EQUAL 0)
        message(FATAL_ERROR "Pinned JSON header download failed: ${JSON_DOWNLOAD_STATUS}")
    endif()
endif()
file(SHA256 "${JSON_HEADER}" JSON_ACTUAL_SHA256)
if(NOT JSON_ACTUAL_SHA256 STREQUAL JSON_SHA256)
    message(FATAL_ERROR "Pinned JSON header SHA256 mismatch")
endif()
add_library(nlohmann_json INTERFACE)
target_include_directories(nlohmann_json INTERFACE "${CMAKE_BINARY_DIR}/_deps/json")

if(JEV_ENABLE_EXPERIMENTAL_HOOK)
    include(FetchContent)
    FetchContent_Declare(detours
        GIT_REPOSITORY https://github.com/microsoft/Detours.git
        GIT_TAG e4bfd6b03e50de46b47abfbd1e46b384f0c5f833)
    FetchContent_MakeAvailable(detours)
    add_library(detours_objects OBJECT
        ${detours_SOURCE_DIR}/src/detours.cpp
        ${detours_SOURCE_DIR}/src/disasm.cpp
        ${detours_SOURCE_DIR}/src/modules.cpp
        ${detours_SOURCE_DIR}/src/image.cpp)
    target_include_directories(detours_objects PUBLIC ${detours_SOURCE_DIR}/src)
    target_compile_definitions(detours_objects PRIVATE DETOURS_X64)
endif()

add_executable(native_host host/main.cpp)
if(JEV_ENABLE_EXPERIMENTAL_HOOK)
    add_library(open_hook SHARED hook/open_hook.cpp)
    target_link_libraries(open_hook PRIVATE detours_objects ole32 shell32)
else()
    add_library(open_hook SHARED hook/disabled_hook.cpp)
endif()
add_library(native_claim SHARED claim/claim.cpp)

target_include_directories(native_host PRIVATE include)
target_include_directories(open_hook PRIVATE include)
target_include_directories(native_claim PRIVATE include)
target_link_libraries(native_host PRIVATE nlohmann_json shell32 user32)
target_link_libraries(open_hook PRIVATE user32)
target_compile_definitions(native_host PRIVATE
    JEV_ENABLE_EXPERIMENTAL_HOOK=$<BOOL:${JEV_ENABLE_EXPERIMENTAL_HOOK}>)

if(MSVC)
    target_compile_options(native_host PRIVATE /W4 /WX /permissive-)
    target_compile_options(open_hook PRIVATE /W4 /WX /permissive-)
    target_compile_options(native_claim PRIVATE /W4 /WX /permissive-)
else()
    target_compile_options(native_host PRIVATE -Wall -Wextra -Werror)
    target_compile_options(open_hook PRIVATE -Wall -Wextra -Werror)
    target_compile_options(native_claim PRIVATE -Wall -Wextra -Werror)
endif()

set_target_properties(native_host native_claim PROPERTIES
    PREFIX ""
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/Release"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_BINARY_DIR}/Release"
)
set_target_properties(open_hook PROPERTIES
    PREFIX ""
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/Release"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_BINARY_DIR}/Release"
)

include(CTest)
if(BUILD_TESTING)
    add_executable(ownership_test tests/ownership_test.cpp)
    target_include_directories(ownership_test PRIVATE include)
    target_link_libraries(ownership_test PRIVATE native_claim)
    set_target_properties(ownership_test PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/Release"
        RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_BINARY_DIR}/Release")
    add_test(NAME ownership_atomicity COMMAND ownership_test)
    add_test(NAME host_self_test COMMAND native_host --self-test)
    set_tests_properties(ownership_atomicity host_self_test PROPERTIES TIMEOUT 30)
    find_package(Python3 COMPONENTS Interpreter QUIET)
    if(Python3_Interpreter_FOUND)
        add_test(NAME host_transport_shutdown
            COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/host_transport_test.py"
                    "$<TARGET_FILE:native_host>")
        set_tests_properties(host_transport_shutdown PROPERTIES TIMEOUT 30)
    endif()
endif()
