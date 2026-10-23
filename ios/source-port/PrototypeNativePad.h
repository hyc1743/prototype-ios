#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>

#ifdef __cplusplus
extern "C" {
#endif

// These bits are the reviewed DmGamepad frontend ABI. Their Xbox placement is
// applied when staging the frontend, before converting to SDL gamepad order.
enum {
    DM_BTN_A = 1u << 0, DM_BTN_B = 1u << 1, DM_BTN_X = 1u << 2, DM_BTN_Y = 1u << 3,
    DM_BTN_LS = 1u << 4, DM_BTN_RS = 1u << 5, DM_BTN_L = 1u << 6, DM_BTN_R = 1u << 7,
    DM_BTN_ZL = 1u << 8, DM_BTN_ZR = 1u << 9, DM_BTN_PLUS = 1u << 10, DM_BTN_MINUS = 1u << 11,
    DM_BTN_LEFT = 1u << 12, DM_BTN_UP = 1u << 13, DM_BTN_RIGHT = 1u << 14, DM_BTN_DOWN = 1u << 15
};
#define DM_BTN_SHAKE (UINT64_C(1) << 63)
#define DM_STICK_MAX 32767
#define DM_GAME_PAD_FULL 1
#define LOG(format, ...) fprintf(stderr, "[prototype-pad] " format "\n", ##__VA_ARGS__)

typedef struct { uint32_t id; float x, y; } dm_touch;
typedef struct SDL_Window SDL_Window;

void dm_input_set(uint64_t buttons, int32_t lx, int32_t ly, int32_t rx, int32_t ry);
// Prototype's source Xbox frontend consumes gamepad input. Raw touchscreen and
// motion/shake events are not connected to any source game touchscreen ABI.
void dm_touch_set(const dm_touch *touches, int count);
void dm_gamepad_reset(void);
void prototype_install_touch_controls(SDL_Window *window);

static inline int32_t PrototypePadClamp(int32_t value) {
    return value < -32767 ? -32767 : value > 32767 ? 32767 : value;
}

// SDL standard order: left XY, right XY, left/right trigger; A/B/X/Y,
// back/guide/start, L3/R3, LB/RB, D-pad up/down/left/right. SDL joystick trigger
// axes use -32768 when released, which SDL gamepads expose as trigger value 0.
static inline void PrototypePadMapState(uint64_t buttons, const int32_t sticks[4],
                                       int16_t axes[6], uint8_t output[15]) {
    static const uint64_t masks[15] = {
        DM_BTN_A, DM_BTN_B, DM_BTN_X, DM_BTN_Y, DM_BTN_MINUS, 0, DM_BTN_PLUS,
        DM_BTN_LS, DM_BTN_RS, DM_BTN_L, DM_BTN_R, DM_BTN_UP, DM_BTN_DOWN, DM_BTN_LEFT, DM_BTN_RIGHT
    };
    axes[0] = (int16_t)PrototypePadClamp(sticks[0]);
    axes[1] = (int16_t)-PrototypePadClamp(sticks[1]);
    axes[2] = (int16_t)PrototypePadClamp(sticks[2]);
    axes[3] = (int16_t)-PrototypePadClamp(sticks[3]);
    axes[4] = (buttons & DM_BTN_ZL) ? 32767 : -32768;
    axes[5] = (buttons & DM_BTN_ZR) ? 32767 : -32768;
    for (int i = 0; i < 15; ++i) output[i] = (buttons & masks[i]) != 0;
}

#ifdef __cplusplus
}
#endif

#ifdef __OBJC__
#import <UIKit/UIKit.h>
#ifdef __cplusplus
extern "C" {
#endif
UIView *dm_gamepad_view(CGRect frame);
#ifdef __cplusplus
}
#endif
#endif
