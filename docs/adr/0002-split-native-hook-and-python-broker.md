# Split the native hook from the Python broker

The injected DLL will only filter, serialize, acknowledge, and fall back; Jev, IM extraction, persistence, and UI live in a separate Python broker. This prevents slow or failure-prone decision work from running inside arbitrary target processes and creates a narrow, testable IPC seam.
