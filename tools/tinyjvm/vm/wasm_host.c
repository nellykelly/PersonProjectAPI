/* The WebAssembly host for the Tiny JVM C interpreter (build spec
 * section 8) -- the browser counterpart to host_native.c. Same vm.c,
 * unmodified; only the VMHost callbacks differ, exactly the point of
 * designing that seam in the first place (section 7.2).
 *
 * No malloc anywhere, matching the VM core itself: the loaded program
 * lives in one static buffer sized generously for these sample-scale
 * programs, and there is exactly one VM instance -- the page only ever
 * runs one program at a time. Every exported function is a plain int
 * (or a pointer to a static string) so the JS side needs no struct
 * marshalling, just Module._name(...) calls or a cwrap wrapper.
 */
#include <emscripten.h>

#include "vm.h"

#define TVM_FILE_BUFFER_SIZE 4096

static VM g_vm;
static uint8_t g_program_buffer[TVM_FILE_BUFFER_SIZE];

/* Each of these calls into a page-defined JS function if one exists, and
 * is a silent no-op otherwise -- so the module still loads and runs even
 * before stepper.js wires up real handlers (useful for testing the WASM
 * build on its own, e.g. from Node, before touching the browser UI). */

EM_JS(void, js_print_int, (int32_t value), {
    if (typeof globalThis.tinyJvmOnPrint === "function") {
        globalThis.tinyJvmOnPrint(value);
    }
});

EM_JS(void, js_pin_mode, (int pin, int mode), {
    if (typeof globalThis.tinyJvmOnPinMode === "function") {
        globalThis.tinyJvmOnPinMode(pin, mode);
    }
});

EM_JS(void, js_digital_write, (int pin, int value), {
    if (typeof globalThis.tinyJvmOnDigitalWrite === "function") {
        globalThis.tinyJvmOnDigitalWrite(pin, value);
    }
});

EM_JS(int, js_digital_read, (int pin), {
    if (typeof globalThis.tinyJvmOnDigitalRead === "function") {
        return globalThis.tinyJvmOnDigitalRead(pin) | 0;
    }
    return 0;
});

static const VMHost WASM_HOST = {
    .pin_mode = js_pin_mode,
    .digital_write = js_digital_write,
    .digital_read = js_digital_read,
    .print_int = js_print_int,
};

EMSCRIPTEN_KEEPALIVE
uint8_t *tinyjvm_get_buffer(void) {
    return g_program_buffer;
}

EMSCRIPTEN_KEEPALIVE
int tinyjvm_get_buffer_size(void) {
    return TVM_FILE_BUFFER_SIZE;
}

/* JS writes the compiled .tvm bytes into the buffer above (via
 * HEAPU8.set at the pointer tinyjvm_get_buffer() returns), then calls
 * this with the byte count. Returns the VMStatus after vm_load --
 * VM_RUNNING on success, a VM_ERR_* on a malformed file. */
EMSCRIPTEN_KEEPALIVE
int tinyjvm_load(int len) {
    if (len < 0 || (size_t)len > sizeof(g_program_buffer)) {
        return VM_ERR_TRUNCATED;
    }
    return (int)vm_load(&g_vm, g_program_buffer, (size_t)len, &WASM_HOST);
}

EMSCRIPTEN_KEEPALIVE
int tinyjvm_step(void) {
    return (int)vm_step(&g_vm);
}

/* Not used by the stepper UI (which always steps one instruction at a
 * time so it can render the state in between), but useful for a plain
 * "Run" button and for smoke-testing the module from Node without a
 * page at all. */
EMSCRIPTEN_KEEPALIVE
int tinyjvm_run(void) {
    return (int)vm_run(&g_vm);
}

EMSCRIPTEN_KEEPALIVE
int tinyjvm_get_status(void) {
    return (int)g_vm.status;
}

EMSCRIPTEN_KEEPALIVE
const char *tinyjvm_status_string(void) {
    return vm_status_string(g_vm.status);
}

EMSCRIPTEN_KEEPALIVE
int tinyjvm_get_ip(void) {
    return g_vm.ip;
}

EMSCRIPTEN_KEEPALIVE
int tinyjvm_get_sp(void) {
    return g_vm.sp;
}

EMSCRIPTEN_KEEPALIVE
int tinyjvm_get_stack_value(int index) {
    if (index < 0 || index >= g_vm.sp) {
        return 0;
    }
    return g_vm.stack[index];
}

EMSCRIPTEN_KEEPALIVE
int tinyjvm_get_frame_count(void) {
    return g_vm.fp;
}

EMSCRIPTEN_KEEPALIVE
int tinyjvm_get_local(int slot) {
    if (g_vm.fp <= 0 || slot < 0 || slot >= TVM_LOCALS_MAX) {
        return 0;
    }
    return g_vm.frames[g_vm.fp - 1].locals[slot];
}
