#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN
FOUNDATION_EXPORT NSString *const PrototypeResourceImportErrorDomain;

/* Call installWithProgress on a background queue. Progress runs on that same
 * queue and is throttled; UIKit callers dispatch UI changes to the main queue. */
@interface PrototypeResourceInstaller : NSObject
@property(nonatomic, readonly) NSURL *gameURL;
- (nullable instancetype)initWithManifestURL:(NSURL *)manifestURL
                              documentsURL:(NSURL *)documentsURL
                                     error:(NSError **)error;
- (BOOL)installWithProgress:(nullable void (^)(NSString *phase, double fraction))progress
                     error:(NSError **)error;
@end
NS_ASSUME_NONNULL_END
