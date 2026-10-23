#import <UIKit/UIKit.h>

@interface PrototypeImportViewController : UIViewController
- (instancetype)initWithReady:(BOOL (^)(void))ready;
@end
