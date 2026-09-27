#include "protocol.hpp"

#include <cstring>
#include <cwchar>

extern "C" __declspec(dllexport) BOOL WINAPI ClaimOpenRequest(
    LPCWSTR mapping_name, DWORD source_pid, const char* payload, DWORD payload_length) {
    if (mapping_name == nullptr || payload == nullptr || payload_length == 0 ||
        payload_length > jev_open::kMaxRequestBytes ||
        std::wcsncmp(mapping_name, jev_open::kOwnershipPrefix,
                     std::wcslen(jev_open::kOwnershipPrefix)) != 0) {
        return FALSE;
    }
    const HANDLE mapping = OpenFileMappingW(FILE_MAP_READ | FILE_MAP_WRITE, FALSE, mapping_name);
    if (mapping == nullptr) {
        return FALSE;
    }
    auto* header = static_cast<jev_open::OwnershipHeader*>(
        MapViewOfFile(mapping, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, jev_open::kMappingBytes));
    bool accepted = false;
    if (header != nullptr) {
        const auto now = GetTickCount64();
        if (header->version == jev_open::kProtocolVersion &&
            header->source_pid == source_pid && header->payload_length == payload_length &&
            now < header->deadline_ms && header->deadline_ms - now <= 1000 &&
            std::memcmp(header + 1, payload, payload_length) == 0) {
            accepted = InterlockedCompareExchange(&header->state, jev_open::kAccepted,
                                                  jev_open::kPending) == jev_open::kPending;
        }
        UnmapViewOfFile(header);
    }
    CloseHandle(mapping);
    return accepted ? TRUE : FALSE;
}
