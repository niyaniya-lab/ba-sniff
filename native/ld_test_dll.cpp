// ld_test_dll.cpp -- exposes ld_length() to the Python test harness.
//
// The decoder in lengthdis.h reads memory at the pointer it's given. The test
// allocates a byte buffer in Python, hands us its address, and we return the
// decoded length. Compiled to ld_test.dll and loaded via ctypes by ld_test.py.
#include "lengthdis.h"

extern "C" __declspec(dllexport) size_t ld_length_at(const void* p) {
    return ld_length(p);
}
