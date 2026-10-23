// SDL owns UIApplication and its native windows. The SDK's C++ entry point is
// compiled with -Dmain=prototype_game_main; this file keeps the real main.
#include <TargetConditionals.h>

#if TARGET_OS_IOS
#define SDL_MAIN_HANDLED 1
#include <SDL3/SDL.h>
#include <SDL3/SDL_main.h>

#import <UIKit/UIKit.h>
#import <CoreFoundation/CoreFoundation.h>
#import "PrototypeImportViewController.h"

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <fcntl.h>
#include <string>
#include <unistd.h>
#include <vector>

// Deliberately C++ linkage, matching windowed_app_main_sdl.cpp.
int prototype_game_main(int argc, char **argv);

// The optional touch frontend installs on the actual game window, after SDL
// creates it. It must tolerate repeated shown events for the same window.
// An absent implementation provides no virtual controller.
extern "C" void prototype_install_touch_controls(SDL_Window *) __attribute__((weak_import));

@interface PrototypeSDLStartupError : UIViewController
@property(nonatomic, copy) NSString *message;
@property(nonatomic, copy) void (^retry)(void);
@end

static void AddCredits(UIViewController *controller) {
    UILabel *credits = [UILabel new];
    credits.text = @"Switch port: fungalspores · Native renderer: penettrator\nReXGlue contributors · Apple graphics: MoltenVK";
    credits.font = [UIFont systemFontOfSize:12];
    credits.textColor = UIColor.secondaryLabelColor;
    credits.textAlignment = NSTextAlignmentCenter;
    credits.numberOfLines = 0;
    credits.translatesAutoresizingMaskIntoConstraints = NO;
    [controller.view addSubview:credits];
    [NSLayoutConstraint activateConstraints:@[
        [credits.centerXAnchor constraintEqualToAnchor:controller.view.safeAreaLayoutGuide.centerXAnchor],
        [credits.bottomAnchor constraintEqualToAnchor:controller.view.safeAreaLayoutGuide.bottomAnchor constant:-8],
        [credits.widthAnchor constraintLessThanOrEqualToAnchor:controller.view.safeAreaLayoutGuide.widthAnchor multiplier:0.94]
    ]];
}

@implementation PrototypeSDLStartupError
- (void)viewDidLoad {
    [super viewDidLoad];
    self.view.backgroundColor = UIColor.systemBackgroundColor;
    UILabel *title = [UILabel new];
    title.text = @"Prototype 启动未完成";
    title.font = [UIFont boldSystemFontOfSize:25];
    title.textAlignment = NSTextAlignmentCenter;
    UILabel *message = [UILabel new];
    message.text = [self.message stringByAppendingString:@"\n\n可在“文件 → 我的 iPhone → Prototype”查看 log.txt 和 engine.log，修正后重试。"];
    message.numberOfLines = 0;
    message.textAlignment = NSTextAlignmentCenter;
    UIButton *retry = [UIButton buttonWithType:UIButtonTypeSystem];
    [retry setTitle:@"重新检查 / 重试启动" forState:UIControlStateNormal];
    [retry addTarget:self action:@selector(retryStartup) forControlEvents:UIControlEventTouchUpInside];
    UIStackView *stack = [[UIStackView alloc] initWithArrangedSubviews:@[title, message, retry]];
    stack.axis = UILayoutConstraintAxisVertical;
    stack.spacing = 18;
    stack.translatesAutoresizingMaskIntoConstraints = NO;
    [self.view addSubview:stack];
    [NSLayoutConstraint activateConstraints:@[
        [stack.centerXAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.centerXAnchor],
        [stack.centerYAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.centerYAnchor],
        [stack.widthAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.widthAnchor multiplier:0.86],
        [stack.topAnchor constraintGreaterThanOrEqualToAnchor:self.view.safeAreaLayoutGuide.topAnchor constant:12],
        [stack.bottomAnchor constraintLessThanOrEqualToAnchor:self.view.safeAreaLayoutGuide.bottomAnchor constant:-52]
    ]];
    AddCredits(self);
}
- (void)retryStartup { if (self.retry) self.retry(); }
- (UIInterfaceOrientationMask)supportedInterfaceOrientations { return UIInterfaceOrientationMaskLandscape; }
@end

static UIWindow *NativeWindow(SDL_Window *window) {
    if (!window) return nil;
    void *pointer = SDL_GetPointerProperty(SDL_GetWindowProperties(window), SDL_PROP_WINDOW_UIKIT_WINDOW_POINTER, nullptr);
    return (__bridge UIWindow *)pointer;
}

// This nested loop is used only while the SDK has no running game. UIKit and
// main-queue importer callbacks continue to run, including foreground retries.
static bool PumpStartupUI(void) {
    bool running = true;
    if (SDL_WasInit(SDL_INIT_VIDEO)) {
        SDL_PumpEvents();
        SDL_Event event;
        while (SDL_PeepEvents(&event, 1, SDL_GETEVENT, SDL_EVENT_FIRST, SDL_EVENT_LAST) > 0)
            if (event.type == SDL_EVENT_QUIT || event.type == SDL_EVENT_TERMINATING) running = false;
    }
    CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.01, true);
    return running;
}

static bool WaitForRetry(NSString *message, SDL_Window *sdlWindow = nullptr) {
    NSLog(@"Prototype startup: %@", message);
    UIWindow *window = NativeWindow(sdlWindow);
    if (!window) {
        for (UIScene *scene in UIApplication.sharedApplication.connectedScenes) {
            if (![scene isKindOfClass:UIWindowScene.class]) continue;
            for (UIWindow *candidate in ((UIWindowScene *)scene).windows)
                if (candidate.isKeyWindow) window = candidate;
        }
    }
    BOOL temporaryWindow = window == nil;
    if (!window) {
        UIWindowScene *availableScene = nil;
        for (UIScene *scene in UIApplication.sharedApplication.connectedScenes)
            if ([scene isKindOfClass:UIWindowScene.class] && scene.activationState == UISceneActivationStateForegroundActive)
                availableScene = (UIWindowScene *)scene;
        window = availableScene ? [[UIWindow alloc] initWithWindowScene:availableScene]
                                : [[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];
    }
    UIViewController *previous = window.rootViewController;
    __block BOOL retry = NO;
    PrototypeSDLStartupError *controller = [PrototypeSDLStartupError new];
    controller.message = message ?: @"未知启动错误";
    controller.retry = ^{ retry = YES; };
    window.rootViewController = controller;
    [window makeKeyAndVisible];
    bool running = true;
    while (!retry && running) {
        @autoreleasepool { running = PumpStartupUI(); }
    }
    controller.retry = nil;
    window.rootViewController = previous;
    if (temporaryWindow) window.hidden = YES;
    return running && retry;
}

static NSError *StartupError(NSString *message) {
    return [NSError errorWithDomain:@"PrototypeSDLStartup" code:1
        userInfo:@{NSLocalizedDescriptionKey: message}];
}

static bool PrepareDocuments(NSURL **documents, NSError **error) {
    NSFileManager *files = NSFileManager.defaultManager;
    NSURL *root = [files URLsForDirectory:NSDocumentDirectory inDomains:NSUserDomainMask].firstObject;
    if (!root) { *error = StartupError(@"无法找到应用文档目录。"); return false; }
    if (![files createDirectoryAtURL:root withIntermediateDirectories:YES attributes:nil error:error]) return false;
    for (NSString *name in @[@"game", @"saves", @"cache", @"ResourcePacks"])
        if (![files createDirectoryAtURL:[root URLByAppendingPathComponent:name isDirectory:YES]
            withIntermediateDirectories:YES attributes:nil error:error]) return false;
    for (NSString *name in @[@"game", @"cache", @"ResourcePacks"])
        [[root URLByAppendingPathComponent:name isDirectory:YES] setResourceValue:@YES
            forKey:NSURLIsExcludedFromBackupKey error:NULL];

    static bool logsPrepared = false;
    if (!logsPrepared) {
        NSURL *log = [root URLByAppendingPathComponent:@"log.txt"];
        NSURL *previous = [root URLByAppendingPathComponent:@"log.prev.txt"];
        if ([files fileExistsAtPath:log.path]) {
            if ([files fileExistsAtPath:previous.path] && ![files removeItemAtURL:previous error:error]) return false;
            if (![files moveItemAtURL:log toURL:previous error:error]) return false;
        }
        int fd = open(log.fileSystemRepresentation, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
        if (fd < 0 || dup2(fd, STDERR_FILENO) < 0 || dup2(fd, STDOUT_FILENO) < 0) {
            int saved = errno;
            if (fd >= 0 && fd != STDERR_FILENO && fd != STDOUT_FILENO) close(fd);
            *error = StartupError([NSString stringWithFormat:@"无法创建启动日志：%s", strerror(saved)]);
            return false;
        }
        if (fd != STDERR_FILENO && fd != STDOUT_FILENO) close(fd);
        setvbuf(stdout, nullptr, _IOLBF, 0);
        setvbuf(stderr, nullptr, _IOLBF, 0);
        logsPrepared = true;
        fprintf(stderr, "[prototype-source] native iOS SDL startup\n");
    }

    NSURL *config = [root URLByAppendingPathComponent:@"prototype.toml"];
    BOOL directory = NO;
    if ([files fileExistsAtPath:config.path isDirectory:&directory]) {
        if (directory) { *error = StartupError(@"prototype.toml 必须是配置文件，不能是文件夹。"); return false; }
    } else {
        NSURL *bundled = [NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:@"dm/prototype.toml"];
        if (![files copyItemAtURL:bundled toURL:config error:error]) return false;
    }
    NSString *game = [root.path stringByAppendingPathComponent:@"game"];
    NSString *saves = [root.path stringByAppendingPathComponent:@"saves"];
    // PrototypeApp::OnConfigurePaths applies CONFIG_PATH before its existing
    // nonempty-game-root return; the SDK otherwise looks inside the app bundle.
    if (setenv("PROTOTYPE_DOCUMENTS_ROOT", root.fileSystemRepresentation, 1) ||
        setenv("PROTOTYPE_GAME_ROOT", game.fileSystemRepresentation, 1) ||
        setenv("PROTOTYPE_SAVE_ROOT", saves.fileSystemRepresentation, 1) ||
        setenv("PROTOTYPE_CONFIG_PATH", config.fileSystemRepresentation, 1) || chdir(root.fileSystemRepresentation)) {
        *error = StartupError([NSString stringWithFormat:@"无法配置游戏目录：%s", strerror(errno)]);
        return false;
    }
    *documents = root;
    return true;
}

static bool ImportResources(SDL_Window *sdlWindow) {
    UIWindow *window = NativeWindow(sdlWindow);
    if (!window) return false;
    UIViewController *previous = window.rootViewController;
    __block BOOL ready = NO;
    PrototypeImportViewController *controller = [[PrototypeImportViewController alloc] initWithReady:^{
        ready = YES;
        return YES;
    }];
    window.rootViewController = controller;
    [window makeKeyAndVisible];
    AddCredits(controller);
    bool running = true;
    while (!ready && running) {
        @autoreleasepool { running = PumpStartupUI(); }
    }
    window.rootViewController = previous;
    return running && ready;
}

static bool SDLCALL GameWindowShown(void *, SDL_Event *event) {
    if (event->type == SDL_EVENT_WINDOW_SHOWN && prototype_install_touch_controls) {
        SDL_WindowID windowID = event->window.windowID;
        dispatch_async(dispatch_get_main_queue(), ^{
            SDL_Window *window = SDL_GetWindowFromID(windowID);
            if (window && prototype_install_touch_controls) prototype_install_touch_controls(window);
        });
    }
    return true;
}

static int SDLCALL PrototypeSDLMain(int argc, char **argv) {
    @autoreleasepool {
        for (;;) {
            NSError *error = nil;
            NSURL *documents = nil;
            if (!PrepareDocuments(&documents, &error)) {
                if (!WaitForRetry(error.localizedDescription)) return EXIT_SUCCESS;
                continue;
            }
            SDL_SetHintWithPriority(SDL_HINT_VIDEO_DRIVER, "uikit", SDL_HINT_OVERRIDE);
            SDL_SetHint(SDL_HINT_APP_NAME, "Prototype");
            // GameController input is merged with touch by the virtual pad.
            // Keep one logical player instead of also opening each pad in SDL.
            SDL_SetHint(SDL_HINT_JOYSTICK_MFI, "0");
            SDL_SetHint(SDL_HINT_ORIENTATIONS, "LandscapeLeft LandscapeRight");
            if (!SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMEPAD)) {
                NSString *message = [NSString stringWithFormat:@"SDL 初始化失败：%s", SDL_GetError()];
                SDL_Quit();
                if (!WaitForRetry(message)) return EXIT_SUCCESS;
                continue;
            }
            SDL_Window *window = SDL_CreateWindow("Prototype resources", 1280, 720,
                SDL_WINDOW_FULLSCREEN | SDL_WINDOW_HIGH_PIXEL_DENSITY);
            if (!window || !NativeWindow(window)) {
                NSString *message = [NSString stringWithFormat:@"无法创建 iOS 游戏窗口：%s", SDL_GetError()];
                bool retry = WaitForRetry(message, window);
                if (window) SDL_DestroyWindow(window);
                SDL_Quit();
                if (!retry) return EXIT_SUCCESS;
                continue;
            }
            bool ready = ImportResources(window);
            SDL_DestroyWindow(window);
            if (!ready) { SDL_Quit(); return EXIT_SUCCESS; }

            std::vector<std::string> arguments;
            arguments.emplace_back(argc > 0 && argv && argv[0] ? argv[0] : "Prototype");
            for (int i = 1; i < argc; ++i) if (argv[i]) arguments.emplace_back(argv[i]);
            arguments.emplace_back("--game_data_root=" + std::string(documents.fileSystemRepresentation) + "/game");
            arguments.emplace_back("--user_data_root=" + std::string(documents.fileSystemRepresentation) + "/saves");
            arguments.emplace_back("--cache_root=" + std::string(documents.fileSystemRepresentation) + "/cache");
            arguments.emplace_back("--log_file=" + std::string(documents.fileSystemRepresentation) + "/engine.log");
            arguments.emplace_back("--video_driver=uikit");
            std::vector<char *> pointers;
            for (std::string &argument : arguments) pointers.push_back(argument.data());
            pointers.push_back(nullptr);
            bool watching = SDL_AddEventWatch(GameWindowShown, nullptr);
            NSString *failure = nil;
            int result = EXIT_FAILURE;
            try {
                result = prototype_game_main(static_cast<int>(arguments.size()), pointers.data());
            } catch (const std::exception &exception) {
                failure = [NSString stringWithFormat:@"游戏启动异常：%s", exception.what()];
            } catch (...) {
                failure = @"游戏启动遇到未知异常。";
            }
            if (watching) SDL_RemoveEventWatch(GameWindowShown, nullptr);
            fprintf(stderr, "[prototype-source] SDK entry returned %d\n", result);
            if (!failure && result == EXIT_SUCCESS) { SDL_Quit(); return result; }
            if (!failure) failure = [NSString stringWithFormat:@"游戏启动未完成（返回码 %d）。", result];
            bool retry = WaitForRetry(failure);
            SDL_Quit();
            if (!retry) return result;
        }
    }
}

int main(int argc, char **argv) {
    return SDL_RunApp(argc, argv, PrototypeSDLMain, nullptr);
}
#endif  // TARGET_OS_IOS
