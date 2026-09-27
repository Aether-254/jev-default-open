#pragma once

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <cstddef>
#include <cstdint>

namespace jev_open {

inline constexpr std::uint32_t kProtocolVersion = 2;
inline constexpr std::uint32_t kAckDeadlineMs = 50;
inline constexpr std::size_t kMaxRequestBytes = 128 * 1024;
inline constexpr wchar_t kHostWindowClass[] = L"JevDefaultOpenNativeHostV2";
inline constexpr wchar_t kHostWindowTitle[] = L"JevDefaultOpenNativeHostWindowV2";
inline constexpr wchar_t kOwnershipPrefix[] = L"Local\\JevOpenRequest-";
inline constexpr ULONG_PTR kCopyDataOpenRequest = 0x4A455650;
inline constexpr int kEmergencyHotkeyId = 0x4A45;
inline constexpr LONG kPending = 0;
inline constexpr LONG kAccepted = 1;
inline constexpr LONG kCancelled = 2;

// A transport timeout must not leave both the source and broker owning a launch.
struct alignas(8) OwnershipHeader {
    std::uint32_t version;
    std::uint32_t source_pid;
    std::uint64_t deadline_ms;
    volatile LONG state;
    std::uint32_t payload_length;
};
static_assert(sizeof(OwnershipHeader) == 24);
static_assert(offsetof(OwnershipHeader, state) == 16);
inline constexpr std::size_t kMappingBytes = sizeof(OwnershipHeader) + kMaxRequestBytes;

}  // namespace jev_open
