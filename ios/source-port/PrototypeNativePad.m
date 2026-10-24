#include "PrototypeNativePad.h"
#include "../runtime/PrototypeVulkan.h"
#include <SDL3/SDL.h>
#include <pthread.h>
#include <string.h>
#import <objc/runtime.h>

static pthread_mutex_t StateMutex = PTHREAD_MUTEX_INITIALIZER;
static uint64_t Buttons;
static int32_t Sticks[4];
static bool Enabled, Suspended;

typedef struct {
    SDL_Joystick *joystick;
    SDL_JoystickID id;
    SDL_WindowID windowID;
} PadDevice;
// Device lifetime and all SDL handle accesses are serialized by SDL's joystick
// lock. StateMutex is only taken after that lock, never while acquiring it.
static PadDevice *Device;

void dm_input_set(uint64_t buttons, int32_t lx, int32_t ly, int32_t rx, int32_t ry) {
    pthread_mutex_lock(&StateMutex);
    Buttons = Enabled && !Suspended ? buttons & ~DM_BTN_SHAKE : 0;
    Sticks[0] = Enabled && !Suspended ? PrototypePadClamp(lx) : 0;
    Sticks[1] = Enabled && !Suspended ? PrototypePadClamp(ly) : 0;
    Sticks[2] = Enabled && !Suspended ? PrototypePadClamp(rx) : 0;
    Sticks[3] = Enabled && !Suspended ? PrototypePadClamp(ry) : 0;
    pthread_mutex_unlock(&StateMutex);
}

void dm_touch_set(const dm_touch *touches, int count) {
    (void)touches; (void)count; // No source Xbox touchscreen ABI is implemented.
}

static void SDLCALL UpdateVirtualPad(void *userdata) {
    PadDevice *device = userdata;
    SDL_LockJoysticks();
    if (Device == device && device->joystick) {
        uint64_t buttons;
        int32_t sticks[4];
        pthread_mutex_lock(&StateMutex);
        buttons = Buttons;
        memcpy(sticks, Sticks, sizeof sticks);
        pthread_mutex_unlock(&StateMutex);
        int16_t axes[6]; uint8_t pressed[15];
        PrototypePadMapState(buttons, sticks, axes, pressed);
        for (int axis = 0; axis < 6; ++axis) SDL_SetJoystickVirtualAxis(device->joystick, axis, axes[axis]);
        for (int button = 0; button < 15; ++button) SDL_SetJoystickVirtualButton(device->joystick, button, pressed[button] != 0);
    }
    SDL_UnlockJoysticks();
}

static void SDLCALL CleanupVirtualPad(void *userdata) {
    PadDevice *device = userdata;
    SDL_LockJoysticks();
    if (Device == device) {
        Device = NULL;
        pthread_mutex_lock(&StateMutex);
        Enabled = false; Buttons = 0; memset(Sticks, 0, sizeof Sticks);
        pthread_mutex_unlock(&StateMutex);
    }
    // SDL can also invoke Cleanup on an allocation failure inside Attach.
    // Until Attach has returned an ID, its caller still owns this allocation.
    if (device->id) free(device);
    SDL_UnlockJoysticks();
}

static void ReleaseDevice(SDL_WindowID windowID) {
    SDL_LockJoysticks();
    PadDevice *device = Device;
    if (device && device->windowID == windowID) {
        Device = NULL;
        pthread_mutex_lock(&StateMutex);
        Enabled = false; Buttons = 0; memset(Sticks, 0, sizeof Sticks);
        pthread_mutex_unlock(&StateMutex);
        SDL_Joystick *joystick = device->joystick;
        SDL_JoystickID id = device->id;
        device->joystick = NULL;
        if (joystick) SDL_CloseJoystick(joystick);
        SDL_DetachVirtualJoystick(id); // Cleanup owns the userdata allocation.
    }
    SDL_UnlockJoysticks();
}

static bool AttachDevice(SDL_WindowID windowID) {
    SDL_LockJoysticks();
    PadDevice *device = calloc(1, sizeof *device);
    if (!device) { SDL_UnlockJoysticks(); return false; }
    device->windowID = windowID;
    SDL_VirtualJoystickDesc desc;
    SDL_INIT_INTERFACE(&desc);
    desc.type = SDL_JOYSTICK_TYPE_GAMEPAD;
    desc.vendor_id = 0x1209; desc.product_id = 0x5052;
    desc.naxes = 6; desc.nbuttons = 15;
    desc.axis_mask = (1u << 6) - 1; desc.button_mask = (1u << 15) - 1;
    desc.name = "Prototype touch and GameController";
    desc.userdata = device; desc.Update = UpdateVirtualPad; desc.Cleanup = CleanupVirtualPad;
    device->id = SDL_AttachVirtualJoystick(&desc);
    if (!device->id) { free(device); SDL_UnlockJoysticks(); return false; }
    Device = device;
    device->joystick = SDL_OpenJoystick(device->id);
    if (!device->joystick || !SDL_SetJoystickPlayerIndex(device->joystick, 0)) {
        ReleaseDevice(windowID);
        SDL_UnlockJoysticks(); return false;
    }
    pthread_mutex_lock(&StateMutex);
    Enabled = true; Buttons = 0; memset(Sticks, 0, sizeof Sticks);
    pthread_mutex_unlock(&StateMutex);
    UpdateVirtualPad(device);
    SDL_UnlockJoysticks();
    return true;
}

@protocol PrototypeDmPadLifecycle <NSObject>
- (void)padsChanged;
- (void)reset;
@end

@interface PrototypeNativePadAttachment : NSObject
@property(nonatomic, weak) UIWindow *window;
@property(nonatomic, strong) UIView *overlay;
@property(nonatomic) SDL_WindowID windowID;
@property(nonatomic) BOOL closed;
@property(nonatomic) BOOL watching;
@property(nonatomic, strong) NSMutableArray *observers;
- (void)close;
- (void)setSuspended:(BOOL)suspended;
@end

static __weak PrototypeNativePadAttachment *Attachment;
static char AttachmentKey;

static bool SDLCALL PadWindowEvent(void *userdata, SDL_Event *event) {
    (void)userdata;
    if (event->type == SDL_EVENT_WINDOW_DESTROYED) {
        SDL_WindowID windowID = event->window.windowID;
        dispatch_async(dispatch_get_main_queue(), ^{
            PrototypeNativePadAttachment *attachment = Attachment;
            if (attachment && attachment.windowID == windowID) [attachment close];
        });
    }
    return true;
}

@implementation PrototypeNativePadAttachment
- (void)setSuspended:(BOOL)suspended {
    if (self.closed) return;
    // Metal rejects command-buffer submissions once iOS backgrounds this app.
    // Drain the renderer before the transition and gate queue calls until the
    // app is active again.
    prototype_vk_suspend(suspended);
    SDL_LockJoysticks();
    if (Device && Device->windowID == self.windowID) {
        pthread_mutex_lock(&StateMutex);
        Suspended = suspended; Buttons = 0; memset(Sticks, 0, sizeof Sticks);
        pthread_mutex_unlock(&StateMutex);
        UpdateVirtualPad(Device);
    }
    SDL_UnlockJoysticks();
    dm_gamepad_reset();
    self.overlay.userInteractionEnabled = !suspended;
    if (!suspended && [self.overlay respondsToSelector:@selector(padsChanged)])
        [(id<PrototypeDmPadLifecycle>)self.overlay padsChanged];
}
- (void)close {
    if (_closed) return;
    _closed = YES;
    for (id observer in _observers) [NSNotificationCenter.defaultCenter removeObserver:observer];
    [_observers removeAllObjects];
    if (_watching) {
        SDL_RemoveEventWatch(PadWindowEvent, (__bridge void *)self);
        _watching = NO;
    }
    if (Attachment == self) {
        Attachment = nil;
        dm_gamepad_reset();
    }
    ReleaseDevice(_windowID);
    UIView *overlay = _overlay;
    if (NSThread.isMainThread) [overlay removeFromSuperview];
    else dispatch_async(dispatch_get_main_queue(), ^{ [overlay removeFromSuperview]; });
    _overlay = nil;
}
- (void)dealloc { [self close]; }
@end

void prototype_install_touch_controls(SDL_Window *sdlWindow) {
    if (!sdlWindow) return;
    if (!NSThread.isMainThread) {
        // SDL window APIs, as well as UIKit, belong on the main thread. Treat
        // the incoming pointer only as an identity until it is found in the
        // current live-window list, avoiding a queued dangling-pointer read.
        dispatch_async(dispatch_get_main_queue(), ^{
            int count = 0;
            SDL_Window **windows = SDL_GetWindows(&count);
            for (int i = 0; i < count; ++i)
                if (windows[i] == sdlWindow) { prototype_install_touch_controls(windows[i]); break; }
            SDL_free(windows);
        });
        return;
    }
    SDL_WindowID windowID = SDL_GetWindowID(sdlWindow);
    UIWindow *window = (__bridge UIWindow *)SDL_GetPointerProperty(SDL_GetWindowProperties(sdlWindow),
        SDL_PROP_WINDOW_UIKIT_WINDOW_POINTER, NULL);
    if (!window) { LOG("SDL window has no UIKit window; virtual pad was not installed"); return; }
    PrototypeNativePadAttachment *existing = objc_getAssociatedObject(window, &AttachmentKey);
    if (existing && !existing.closed) { [window bringSubviewToFront:existing.overlay]; return; }
    PrototypeNativePadAttachment *old = Attachment;
    if (old) [old close];
    PrototypeNativePadAttachment *attachment = [PrototypeNativePadAttachment new];
    attachment.window = window; attachment.windowID = windowID;
    attachment.observers = [NSMutableArray array];
    if (!AttachDevice(windowID)) {
        LOG("virtual gamepad initialization failed: %s", SDL_GetError());
        UILabel *notice = [[UILabel alloc] initWithFrame:CGRectInset(window.bounds, 28, 28)];
        notice.text = @"手柄初始化失败，请查看 log.txt 后重新启动。";
        notice.numberOfLines = 0; notice.textColor = UIColor.whiteColor;
        notice.backgroundColor = [UIColor colorWithWhite:0 alpha:0.8];
        notice.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
        [window addSubview:notice];
        return;
    }
    attachment.overlay = dm_gamepad_view(window.bounds);
    [(id<PrototypeDmPadLifecycle>)attachment.overlay reset];
    attachment.overlay.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    [window addSubview:attachment.overlay];
    Attachment = attachment;
    objc_setAssociatedObject(window, &AttachmentKey, attachment, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    attachment.watching = SDL_AddEventWatch(PadWindowEvent, (__bridge void *)attachment);
    __weak PrototypeNativePadAttachment *weak = attachment;
    NSNotificationCenter *notifications = NSNotificationCenter.defaultCenter;
    for (NSNotificationName name in @[UIApplicationWillResignActiveNotification, UIApplicationDidEnterBackgroundNotification,
                                      UISceneWillDeactivateNotification]) {
        id observer = [notifications addObserverForName:name object:nil queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *notification) {
            PrototypeNativePadAttachment *strong = weak;
            if (strong && (![notification.object isKindOfClass:UIWindowScene.class] || notification.object == strong.window.windowScene))
                [strong setSuspended:YES];
        }];
        [attachment.observers addObject:observer];
    }
    for (NSNotificationName name in @[UIApplicationDidBecomeActiveNotification, UISceneDidActivateNotification]) {
        id observer = [notifications addObserverForName:name object:nil queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *notification) {
            PrototypeNativePadAttachment *strong = weak;
            if (strong && (![notification.object isKindOfClass:UIWindowScene.class] || notification.object == strong.window.windowScene))
                [strong setSuspended:NO];
        }];
        [attachment.observers addObject:observer];
    }
    UIWindowScene *scene = window.windowScene;
    [attachment setSuspended:scene ? scene.activationState != UISceneActivationStateForegroundActive
                                   : UIApplication.sharedApplication.applicationState != UIApplicationStateActive];
    LOG("installed SDL virtual gamepad for window %u, player 0", windowID);
}
