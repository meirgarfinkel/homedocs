# Builds llama.cpp (from LLAMA_DIR) and hd_llama, our small wrapper, as shared libraries that find each other
# in their own folder. Run through packaging/build_llama.sh (or .bat), which pins the llama.cpp release.
cmake_minimum_required(VERSION 3.16)
project(hd_llama C CXX)

if(NOT LLAMA_DIR)
  message(FATAL_ERROR "Set LLAMA_DIR to the llama.cpp source folder (packaging/build_llama.sh does)")
endif()

set(CMAKE_POSITION_INDEPENDENT_CODE ON)
# every library looks for the others next to itself
set(CMAKE_BUILD_WITH_INSTALL_RPATH ON)  # (and nothing else: no paths from this machine)
if(APPLE)
  set(CMAKE_INSTALL_RPATH "@loader_path")
  set(CMAKE_INSTALL_NAME_DIR "@rpath")
else()
  set(CMAKE_INSTALL_RPATH "$ORIGIN")
endif()
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/out)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/out)

set(BUILD_SHARED_LIBS ON CACHE BOOL "" FORCE)
add_subdirectory(${LLAMA_DIR} llama)

add_library(hd_llama SHARED hd_llama.c)
target_link_libraries(hd_llama PRIVATE llama ggml)
