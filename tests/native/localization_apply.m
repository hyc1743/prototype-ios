#import <Foundation/Foundation.h>
#import "PrototypeChineseInstaller.h"
#include <stdio.h>

int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc != 3) return 2;
        NSError *error = nil;
        NSURL *bundle = [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[1]] isDirectory:YES];
        NSURL *documents = [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[2]] isDirectory:YES];
        BOOL ok = PrototypeInstallChinese(bundle, documents, ^(NSString *phase, double fraction) {
            if (fraction == 1.0 || ((int)(fraction*1000)) % 100 == 0)
                fprintf(stderr, "[localization-probe] %.1f%% %s\n", fraction*100, phase.UTF8String);
        }, &error);
        if (!ok) fprintf(stderr, "[localization-probe] failed: %s\n", error.localizedDescription.UTF8String);
        return ok ? 0 : 1;
    }
}
