#include <TargetConditionals.h>
#include <stdio.h>
#include <time.h>
#if TARGET_OS_IPHONE
#import <UIKit/UIKit.h>
#endif

/* The Switch splash draws through libnx framebuffer/NWindow. Preserve its
 * authorship on the native UI instead of opening a Horizon display service. */
void prototype_show_credits(void) {
    fprintf(stderr, "[prototype] PROTOTYPE Switch port by fungalspores; t.me/fspores; ReXGlue static recompilation\n");
#if TARGET_OS_IPHONE
    dispatch_async(dispatch_get_main_queue(), ^{
        UIWindow *target = nil;
        for (UIScene *scene in UIApplication.sharedApplication.connectedScenes) {
            if (![scene isKindOfClass:UIWindowScene.class]) continue;
            for (UIWindow *window in ((UIWindowScene *)scene).windows)
                if (window.isKeyWindow) target = window;
        }
        if (!target) return;
        UIView *panel = [[UIView alloc] initWithFrame:target.bounds];
        panel.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
        panel.backgroundColor = UIColor.blackColor;
        UILabel *label = [[UILabel alloc] initWithFrame:CGRectInset(panel.bounds, 40, 40)];
        label.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
        label.numberOfLines = 0; label.textAlignment = NSTextAlignmentCenter;
        label.font = [UIFont systemFontOfSize:21]; label.textColor = UIColor.whiteColor;
        label.text = @"PROTOTYPE\n\nSwitch port by fungalspores\nt.me/fspores\n\nReXGlue static recompilation\n\nApple host: nxnative / MoltenVK";
        [panel addSubview:label]; [target addSubview:panel];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{ [panel removeFromSuperview]; });
    });
    struct timespec delay = {3, 0};
    while (nanosleep(&delay, &delay)) {}
#endif
}
