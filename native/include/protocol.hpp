#pragma once

#include <cstdint>
#include <string>

namespace jev_open {

inline constexpr std::uint32_t kProtocolVersion = 1;
inline constexpr std::uint32_t kPipeAckDeadlineMs = 50;

struct OpenRequest {
    std::wstring request_id;
    std::uint32_t source_pid;
    std::uintptr_t foreground_hwnd;
    std::wstring verb;
    std::wstring target;
    std::wstring parameters;
    std::wstring working_directory;
    std::uint32_t show_command;
    std::uint64_t shell_execute_flags;
};

// TODO: Define bounded length-prefixed UTF-8 serialization.
// TODO: Reject oversized messages before allocating buffers.
// TODO: Authenticate the broker instance with a startup nonce.

}  // namespace jev_open
