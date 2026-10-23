/* Exact-build offsets are recorded by tools/analyze_platform.py. */
#include "PrototypeLibnx.h"
#include "PrototypeFS.h"
#include "PrototypeAudio.h"
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <setjmp.h>

extern uint64_t svc_dispatch(int, uint64_t *, uint64_t);
extern void prototype_bind_guest_hooks(uint8_t *, size_t);
extern void prototype_read_native_pad(uint64_t *, int32_t[4]);
extern void guest_clock_read_wait(void);
extern void prototype_show_credits(void);
typedef struct { uint32_t key, flags; uint64_t value[2]; } ConfigEntry;
_Static_assert(sizeof(ConfigEntry) == 24, "libnx homebrew ABI");
static uint8_t *guest_base;
static _Atomic(void (*)(const char *)) error_callback;
static _Thread_local jmp_buf exit_point;
static _Thread_local int exit_point_ready, exit_result;
static void platform_exit(int result) {
    if (!exit_point_ready) pthread_exit((void *)(intptr_t)result);
    exit_result = result;
    longjmp(exit_point, 1);
}
static void loader_return(uint64_t result) {
    fprintf(stderr, "[prototype] libnx returned %#llx\n", (unsigned long long)result);
    pthread_exit((void *)(uintptr_t)result);
}
void prototype_set_error_callback(void (*callback)(const char *)) { atomic_store(&error_callback, callback); }
int prototype_libnx_boot(uint8_t *base, size_t size, uint32_t handle, int argc, char **argv) {
    guest_base = base;
    prototype_bind_guest_hooks(base, size);
    uint64_t registers[32] = {0};
    size_t heap_size = 2048ull << 20;
    char executable[4096], command_line[4100];
    const char *documents = getenv("PROTOTYPE_DOCUMENTS_ROOT");
    snprintf(executable, sizeof executable, "sdmc:/switch/prototype-nx/prototype.nro");
    snprintf(command_line, sizeof command_line, "\"%s\"", executable);
    registers[1] = heap_size;
    svc_dispatch(1, registers, 0);
    if (registers[0] || !registers[1]) return -1;
    ConfigEntry config[] = {
        {1, 0, {handle, 0}}, {3, 0, {registers[1], heap_size}},
        {5, 0, {0, (uintptr_t)command_line}},
        {6, 0, {UINT64_MAX, UINT64_MAX}}, {7, 0, {(uint64_t)(int64_t)-2, 0}},
        {16, 0, {0x0d0000, 0}}, {17, 0, {UINT64_MAX, 0}}, {0, 0, {0, 0}}
    };
    uintptr_t stack;
    __asm__ volatile("mov %0, sp" : "=r"(stack));
    *(uintptr_t *)(base + 0x4022418) = stack;
    fprintf(stderr, "[prototype] libnx init +0x2d07fb0, heap %zu MiB\n", heap_size >> 20);
    ((void (*)(const ConfigEntry *, uint64_t, void *))(base + 0x2d07fb0))(config, UINT64_MAX, loader_return);
    fprintf(stderr, "[prototype] original libnx init completed\n");
    /* The host has already relocated the image; libnx runs its constructors once. */
    char *arguments[] = {executable, NULL};
    (void)argc; (void)argv;
    fprintf(stderr, "[prototype] entering original main +0x58510\n");
    int result;
    exit_point_ready = 1;
    if (!setjmp(exit_point)) {
        result = ((int (*)(int, char **))(base + 0x58510))(1, arguments);
        fprintf(stderr, "[prototype] original main returned %d\n", result);
    } else {
        result = exit_result;
        fprintf(stderr, "[prototype] original main exited %d via native platform\n", result);
    }
    exit_point_ready = 0;
    return result;
}
void prototype_libnx_app_init(void) {
    prototype_fs_install(guest_base);
    fprintf(stderr, "[prototype] iOS platform services installed\n");
}
static uint32_t success(void) { return 0; }
static uint32_t unavailable(void) { return 0xF601; }
static int application_type(void) { return 0; }
static int focus(void) { return 1; }
static int handheld(void) { return 0; }
static int applet_loop(void) { guest_clock_read_wait(); return 1; }
static unsigned char window[4096];
static void *default_window(void) { return window; }
static int valid_window(void *w) { return w != NULL; }
static uint32_t display_resolution(int32_t *width, int32_t *height) {
    if (width) *width = 1280;
    if (height) *height = 720;
    return 0;
}
static uint32_t window_dimensions(void *w, int32_t *width, int32_t *height) {
    (void)w; return display_resolution(width, height);
}
static void *message_event(void) {
    /* A real unsignaled host event, so libnx waitMulti retains its timed wait. */
    static uint32_t event[3];
    if (!event[0]) {
        uint64_t r[32] = {0}; svc_dispatch(0x45, r, 0);
        if (r[0]) abort();
        event[0] = (uint32_t)r[2]; event[1] = (uint32_t)r[1];
    }
    return event;
}
static void pad_initialize(void *p, uint64_t mask) {
    memset(p, 0, 56);
    ((uint8_t *)p)[0] = (uint8_t)mask;
    ((uint8_t *)p)[2] = !!(mask & (1ull << 0x20));
}
static void pad_update(void *p) {
    uint64_t buttons; int32_t axes[4];
    prototype_read_native_pad(&buttons, axes);
    int selected = (((uint8_t *)p)[0] & 1) || ((uint8_t *)p)[2];
    if (!selected) { buttons = 0; memset(axes, 0, sizeof axes); }
    memcpy((uint8_t *)p + 24, (uint8_t *)p + 16, 8);
    memcpy((uint8_t *)p + 16, &buttons, 8);
    memcpy((uint8_t *)p + 32, axes, 16);
    ((uint8_t *)p)[1] = selected && !((uint8_t *)p)[2];
    ((uint8_t *)p)[3] = selected && ((uint8_t *)p)[2];
    uint32_t style = selected ? (((uint8_t *)p)[3] ? 2 : 1) : 0, attributes = !!selected;
    memcpy((uint8_t *)p + 4, &style, 4); memcpy((uint8_t *)p + 8, &attributes, 4);
}
extern int touch_fill_n(void *, int, uint64_t);
extern uint64_t now_ns(void);
static uint32_t pad_style(int id) { return id == 0 || id == 0x20 ? 1 : 0; }
static int hid_states(int id, void *out, int count) {
    if (!out || count <= 0 || (id != 0 && id != 0x20)) return 0;
    uint64_t buttons; int32_t axes[4]; prototype_read_native_pad(&buttons, axes);
    memset(out, 0, 40);
    uint64_t sample = now_ns(); uint32_t attributes = 1;
    memcpy(out, &sample, 8); memcpy((uint8_t *)out + 8, &buttons, 8);
    memcpy((uint8_t *)out + 16, axes, 16); memcpy((uint8_t *)out + 32, &attributes, 4);
    return 1;
}
static int touch_states(void *out, int count) {
    if (!out || count <= 0) return 0;
    touch_fill_n(out, 16, now_ns()); return 1;
}
void *prototype_libnx_resolve(const char *name) {
#define ENTRY(symbol, fn) if (!strcmp(name, symbol)) return (void *)fn
    ENTRY("prototype_libnx_app_init", prototype_libnx_app_init);
    ENTRY("prototype_find_device", prototype_find_device);
    ENTRY("prototype_platform_exit", platform_exit);
    ENTRY("prototype_show_credits", prototype_show_credits);
    ENTRY("prototype_success", success);
    ENTRY("prototype_unavailable", unavailable);
    ENTRY("prototype_application_type", application_type);
    ENTRY("prototype_focus", focus);
    ENTRY("prototype_handheld", handheld);
    ENTRY("prototype_applet_loop", applet_loop);
    ENTRY("prototype_default_window", default_window);
    ENTRY("prototype_valid_window", valid_window);
    ENTRY("prototype_display_resolution", display_resolution);
    ENTRY("prototype_window_dimensions", window_dimensions);
    ENTRY("prototype_message_event", message_event);
    ENTRY("prototype_pad_initialize", pad_initialize);
    ENTRY("prototype_pad_update", pad_update);
    ENTRY("appletGetAppletType", application_type);
    ENTRY("appletHook", success);
    ENTRY("appletUnhook", success);
    ENTRY("appletSetFocusHandlingMode", success);
    ENTRY("appletGetDefaultDisplayResolution", display_resolution);
    ENTRY("appletGetDefaultDisplayResolutionChangeEvent", unavailable);
    ENTRY("appletGetOperationMode", handheld);
    ENTRY("appletGetFocusState", focus);
    ENTRY("appletGetMessageEvent", message_event);
    ENTRY("appletMainLoop", applet_loop);
    ENTRY("nwindowGetDefault", default_window);
    ENTRY("nwindowIsValid", valid_window);
    ENTRY("nwindowGetDimensions", window_dimensions);
    ENTRY("nwindowSetDimensions", success);
    ENTRY("padConfigureInput", success);
    ENTRY("padInitializeWithMask", pad_initialize);
    ENTRY("padUpdate", pad_update);
    ENTRY("hidInitialize", success);
    ENTRY("hidGetNpadStyleSet", pad_style);
    ENTRY("hidInitializeTouchScreen", success);
    ENTRY("hidGetTouchScreenStates", touch_states);
    ENTRY("hidPermitVibration", success);
    ENTRY("hidInitializeVibrationDevices", unavailable);
    ENTRY("hidSendVibrationValues", unavailable);
    if (!strncmp(name, "hidGetNpadStates", 16)) return (void *)hid_states;
#undef ENTRY
    return prototype_audio_resolve(name);
}

/* These Horizon services have no session on Apple. Report their failure;
 * returning success would hand libnx uninitialized handles and IPC outputs. */
int prototype_platform_svc(int number, uint64_t *r) {
    switch (number) {
    case 0x1f: case 0x21: case 0x22:
        r[0] = 0xF601; r[1] = 0; return 1;
    case 0x30: {
        const uint64_t limits[] = {0xCD500000, 256, 4096, 128, 1024};
        if (r[2] >= sizeof limits / sizeof limits[0]) r[0] = 0xF601;
        else { r[1] = limits[r[2]]; r[0] = 0; }
        return 1;
    }
    case 0x31: r[0] = 0; r[1] = 0; return 1;
    default: return 0;
    }
}
