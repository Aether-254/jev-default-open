cmake_minimum_required(VERSION 3.25)
project(jev_default_open_native LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# TODO: FetchContent a pinned Microsoft Detours 4.0.1 revision.
# TODO: Fail configuration for non-Windows or non-x64 generators.
# TODO: Build native_host and open_hook with warnings-as-errors.

add_executable(native_host host/main.cpp)
add_library(open_hook SHARED hook/open_hook.cpp)

target_include_directories(native_host PRIVATE include)
target_include_directories(open_hook PRIVATE include)
