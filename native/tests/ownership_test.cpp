#include "protocol.hpp"

#include <cstring>
#include <iostream>
#include <string>
#include <thread>

extern "C" __declspec(dllimport) BOOL WINAPI ClaimOpenRequest(
    LPCWSTR mapping_name, DWORD source_pid, const char* payload, DWORD payload_length);

int main() {
    const auto name = std::wstring(jev_open::kOwnershipPrefix) +
        std::to_wstring(GetCurrentProcessId()) + L"-00000000-0000-0000-0000-000000000001";
    const HANDLE mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE,
        0, static_cast<DWORD>(jev_open::kMappingBytes), name.c_str());
    if (mapping == nullptr || GetLastError() == ERROR_ALREADY_EXISTS) {
        return 1;
    }
    auto* header = static_cast<jev_open::OwnershipHeader*>(
        MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, jev_open::kMappingBytes));
    if (header == nullptr) {
        CloseHandle(mapping);
        return 2;
    }
    constexpr char payload[] = "{\"synthetic\":true}";
    const DWORD length = sizeof(payload) - 1;
    header->version = jev_open::kProtocolVersion;
    header->source_pid = GetCurrentProcessId();
    header->payload_length = length;
    std::memcpy(header + 1, payload, length);
    int failures = 0;
    auto reset = [&] {
        header->state = jev_open::kPending;
        header->deadline_ms = GetTickCount64() + 500;
    };
    auto claim = [&] {
        return ClaimOpenRequest(name.c_str(), GetCurrentProcessId(), payload, length);
    };
    reset();
    if (!claim() || claim() || header->state != jev_open::kAccepted) {
        ++failures;
    }
    reset();
    header->state = jev_open::kCancelled;
    if (claim()) {
        ++failures;
    }
    reset();
    header->deadline_ms = GetTickCount64() - 1;
    if (claim()) {
        ++failures;
    }
    reset();
    if (ClaimOpenRequest(name.c_str(), GetCurrentProcessId() + 1, payload, length) ||
        ClaimOpenRequest(name.c_str(), GetCurrentProcessId(), "wrong", 5)) {
        ++failures;
    }
    for (int iteration = 0; iteration < 200; ++iteration) {
        reset();
        bool broker_won = false;
        std::thread broker([&] { broker_won = claim() != FALSE; });
        const bool source_won = InterlockedCompareExchange(
            &header->state, jev_open::kCancelled, jev_open::kPending) == jev_open::kPending;
        broker.join();
        if (source_won == broker_won) {
            ++failures;
        }
    }
    UnmapViewOfFile(header);
    CloseHandle(mapping);
    std::cout << "Ownership synthetic tests: " << failures << " failures\n";
    return failures == 0 ? 0 : 3;
}
