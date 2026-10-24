#import "PrototypeChineseInstaller.h"
#import <CommonCrypto/CommonDigest.h>
#include <zlib.h>
#include <stdio.h>
#include <sys/clonefile.h>

static NSError *ChineseError(NSString *message) {
    return [NSError errorWithDomain:@"PrototypeChinese" code:1
        userInfo:@{NSLocalizedDescriptionKey:message}];
}

static NSString *Digest(NSData *data) {
    unsigned char hash[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(data.bytes, (CC_LONG)data.length, hash);
    NSMutableString *result = [NSMutableString new];
    for (unsigned i = 0; i < sizeof(hash); ++i) [result appendFormat:@"%02x", hash[i]];
    return result;
}

static NSData *Inflate(NSData *data, NSError **error) {
    const unsigned char *bytes = data.bytes;
    if (data.length < 2 || bytes[0] != 'R' || bytes[1] != 'Z') return data;
    if (data.length < 16) { *error = ChineseError(@"中文资源原文件不完整。"); return nil; }
    uint32_t size = bytes[8] | (uint32_t)bytes[9] << 8 | (uint32_t)bytes[10] << 16 | (uint32_t)bytes[11] << 24;
    if (size > 64u*1024u*1024u) { *error = ChineseError(@"中文资源原文件大小异常。"); return nil; }
    NSMutableData *out = [NSMutableData dataWithLength:size];
    uLongf length = size;
    if (uncompress(out.mutableBytes, &length, bytes+16, data.length-16) != Z_OK || length != size) {
        *error = ChineseError(@"无法读取中文资源对应的原始文件。"); return nil;
    }
    return out;
}

static NSData *Deflate(NSData *data, NSError **error) {
    uLongf size = compressBound(data.length);
    NSMutableData *out = [NSMutableData dataWithLength:16+size];
    unsigned char *bytes = out.mutableBytes;
    bytes[0] = 'R'; bytes[1] = 'Z';
    uint32_t length = (uint32_t)data.length;
    for (unsigned i = 0; i < 4; ++i) bytes[8+i] = (unsigned char)(length >> (8*i));
    if (compress2(bytes+16, &size, data.bytes, data.length, Z_BEST_SPEED) != Z_OK) {
        *error = ChineseError(@"无法生成中文资源覆盖文件。"); return nil;
    }
    out.length = 16+size;
    return out;
}

static NSURL *Destination(NSURL *game, NSString *path) {
    if (![path isKindOfClass:NSString.class] || path.length == 0 ||
        [path hasPrefix:@"/"] || [path containsString:@"\\"] || [path containsString:@":"]) return nil;
    for (NSString *part in [path componentsSeparatedByString:@"/"])
        if (!part.length || [part isEqualToString:@".."] || [part isEqualToString:@"."]) return nil;
    return [game URLByAppendingPathComponent:path];
}

static BOOL Backup(NSURL *target, NSURL *backups, NSString *path, NSError **error) {
    NSFileManager *files = NSFileManager.defaultManager;
    if (![files fileExistsAtPath:target.path]) return YES;
    NSURL *backup = Destination(backups, path);
    if (!backup) { *error = ChineseError(@"中文资源备份路径无效。"); return NO; }
    if ([files fileExistsAtPath:backup.path]) return YES;
    return [files createDirectoryAtURL:backup.URLByDeletingLastPathComponent
        withIntermediateDirectories:YES attributes:nil error:error] &&
        [files copyItemAtURL:target toURL:backup error:error];
}

static BOOL PrepareChineseView(NSDictionary *manifest, NSURL *original, NSURL *game,
        void (^progress)(NSString *, double), NSError **error) {
    NSFileManager *files = NSFileManager.defaultManager;
    NSUInteger clones = 0, copies = 0;
    NSArray *originals = manifest[@"original_files"];
    if (![originals isKindOfClass:NSArray.class] || originals.count > 1024 || originals.count == 0) {
        *error = ChineseError(@"中文资源视图清单无效。"); return NO;
    }
    for (NSDictionary *entry in originals) {
        NSString *relative = entry[@"path"];
        NSURL *source = Destination(original, relative);
        NSURL *target = Destination(game, relative);
        if (!source || !target) { *error = ChineseError(@"中文资源视图路径无效。"); return NO; }
        NSDictionary *attributes = [files attributesOfItemAtPath:target.path error:NULL];
        if ([attributes[NSFileSize] unsignedLongLongValue] == [entry[@"bytes"] unsignedLongLongValue]) continue;
        if (progress) progress(@"正在准备独立中文资源视图…", 0.0);
        if (![files createDirectoryAtURL:target.URLByDeletingLastPathComponent withIntermediateDirectories:YES attributes:nil error:error]) return NO;
        NSURL *temporary = [target URLByAppendingPathExtension:@"chinese-clone-tmp"];
        if ([files fileExistsAtPath:temporary.path] && ![files removeItemAtURL:temporary error:error]) return NO;
        if (clonefile(source.fileSystemRepresentation, temporary.fileSystemRepresentation, CLONE_NOFOLLOW) == 0) {
            ++clones;
        } else {
            if ([files fileExistsAtPath:temporary.path] && ![files removeItemAtURL:temporary error:error]) return NO;
            if (![files copyItemAtURL:source toURL:temporary error:error]) return NO;
            ++copies;
        }
        if ([files fileExistsAtPath:target.path] && ![files removeItemAtURL:target error:error]) return NO;
        if (![files moveItemAtURL:temporary toURL:target error:error]) return NO;
    }
    for (NSDictionary *table in manifest[@"archive_tables"]) {
        NSURL *target = Destination(game, table[@"archive"]);
        NSData *replacement = [[NSData alloc] initWithBase64EncodedString:table[@"data"] options:0];
        if (!target || !replacement || ![Digest(replacement) isEqual:table[@"sha256"]]) {
            *error = ChineseError(@"中文资源归档索引校验失败。"); return NO;
        }
        NSFileHandle *file = [NSFileHandle fileHandleForUpdatingURL:target error:error];
        if (!file) return NO;
        unsigned long long offset = [table[@"offset"] unsignedLongLongValue];
        NSURL *originalURL = Destination(original, table[@"archive"]);
        NSFileHandle *originalFile = [NSFileHandle fileHandleForReadingFromURL:originalURL error:error];
        if (!originalFile) { [file closeFile]; return NO; }
        [originalFile seekToFileOffset:offset];
        if (![Digest([originalFile readDataOfLength:replacement.length]) isEqual:table[@"source_sha256"]]) {
            [originalFile closeFile]; [file closeFile];
            *error = ChineseError(@"原资源索引不匹配，未改写原资源。"); return NO;
        }
        [file seekToFileOffset:offset];
        NSData *existing = [file readDataOfLength:replacement.length];
        NSString *hash = Digest(existing);
        if (![hash isEqual:table[@"sha256"]]) {
            if (![hash isEqual:table[@"source_sha256"]]) {
                [file closeFile]; [originalFile closeFile];
                *error = ChineseError(@"中文资源视图归档不匹配。"); return NO;
            }
            [file seekToFileOffset:offset];
            [file writeData:replacement];
            [file synchronizeFile];
        }
        [file closeFile];
        [originalFile seekToFileOffset:offset];
        BOOL preserved = [Digest([originalFile readDataOfLength:replacement.length]) isEqual:table[@"source_sha256"]];
        [originalFile closeFile];
        if (!preserved) { *error = ChineseError(@"原资源索引保留校验失败。"); return NO; }
    }
    [game setResourceValue:@YES forKey:NSURLIsExcludedFromBackupKey error:NULL];
    fprintf(stderr, "[prototype-localization] Chinese source view: %lu clones, %lu copies, %lu private archive indexes; original indexes verified unchanged\n",
            (unsigned long)clones, (unsigned long)copies, (unsigned long)[manifest[@"archive_tables"] count]);
    return YES;
}

BOOL PrototypeInstallChinese(NSURL *bundle, NSURL *documents,
        void (^progress)(NSString *, double), NSError **error) {
    @try {
        NSFileManager *files = NSFileManager.defaultManager;
        NSURL *manifestURL = [bundle URLByAppendingPathComponent:@"localization.json"];
        NSData *rawManifest = [NSData dataWithContentsOfURL:manifestURL options:0 error:error];
        if (!rawManifest) return NO;
        NSDictionary *manifest = [NSJSONSerialization JSONObjectWithData:rawManifest options:0 error:error];
        if (![manifest isKindOfClass:NSDictionary.class] || [manifest[@"schema"] intValue] != 1 ||
            ![manifest[@"language"] isEqual:@"zh-Hans"] || ![manifest[@"files"] isKindOfClass:NSArray.class]) {
            *error = ChineseError(@"中文资源清单不匹配。"); return NO;
        }
        NSArray *edits = manifest[@"files"];
        if (edits.count > 20000) { *error = ChineseError(@"中文资源数量异常。"); return NO; }
        NSString *identity = Digest(rawManifest);
        NSURL *originalGame = [documents URLByAppendingPathComponent:@"game" isDirectory:YES];
        NSURL *game = [documents URLByAppendingPathComponent:@"game-zh" isDirectory:YES];
        NSURL *receipt = [documents URLByAppendingPathComponent:@".prototype-chinese-v1"];
        NSString *installed = [NSString stringWithContentsOfURL:receipt encoding:NSUTF8StringEncoding error:NULL];
        NSDictionary *font = manifest[@"font"];
        NSURL *fontTarget = Destination(game, font[@"path"]);
        NSData *fontData = [NSData dataWithContentsOfURL:[bundle URLByAppendingPathComponent:@"fonts_latin.gfx"]
            options:0 error:error];
        if (!fontTarget || !fontData || fontData.length != [font[@"bytes"] unsignedLongLongValue] ||
            ![Digest(fontData) isEqual:font[@"sha256"]]) {
            *error = ChineseError(@"中文字库校验失败。"); return NO;
        }
        BOOL complete = [installed isEqualToString:identity];
        if (complete) {
            for (NSDictionary *entry in manifest[@"original_files"])
                if (![files fileExistsAtPath:Destination(game, entry[@"path"]).path]) { complete = NO; break; }
            for (NSDictionary *edit in edits) {
                NSURL *target = Destination(game, edit[@"path"]);
                if (!target || ![files fileExistsAtPath:target.path]) { complete = NO; break; }
            }
            NSData *actualFont = [NSData dataWithContentsOfURL:fontTarget options:0 error:NULL];
            complete = complete && actualFont && [Digest(actualFont) isEqual:font[@"sha256"]];
        }
        if (complete) {
            fprintf(stderr, "[prototype-localization] zh-Hans ready, %lu text files\n", (unsigned long)edits.count);
            if (progress) progress(@"中文资源就绪", 1.0);
            return YES;
        }
        NSURL *backups = [documents URLByAppendingPathComponent:@"LocalizationBackups/pre-Chinese" isDirectory:YES];
        if (progress) progress(@"正在准备中文文本与字库…", 0.0);
        if (!PrepareChineseView(manifest, originalGame, game, progress, error)) return NO;
        NSMutableDictionary<NSString *, NSFileHandle *> *archives = [NSMutableDictionary new];
        NSUInteger index = 0;
        for (NSDictionary *edit in edits) {
            @autoreleasepool {
                NSString *name = edit[@"path"];
                NSURL *target = Destination(game, name);
                if (!target || (![name hasSuffix:@".p3d.rz"] && ![name hasSuffix:@".p3d"])) {
                    *error = ChineseError(@"中文资源目标路径无效。"); return NO;
                }
                // A previous interrupted installation may already have completed this file.
                NSData *existing = [NSData dataWithContentsOfURL:target options:0 error:NULL];
                NSError *resumeError = nil;
                NSData *decoded = existing ? Inflate(existing, &resumeError) : nil;
                if (decoded && [Digest(decoded) isEqual:edit[@"output_sha256"]]) { ++index; continue; }
                NSString *archiveName = edit[@"archive"];
                if (![archiveName isKindOfClass:NSString.class] || [archiveName containsString:@"/"] ||
                    [archiveName containsString:@"\\"] || ![archiveName hasSuffix:@".rcf"]) {
                    *error = ChineseError(@"中文资源源文件无效。"); return NO;
                }
                NSFileHandle *source = archives[archiveName];
                if (!source) {
                    source = [NSFileHandle fileHandleForReadingFromURL:[originalGame URLByAppendingPathComponent:archiveName] error:error];
                    if (!source) return NO;
                    archives[archiveName] = source;
                }
                NSUInteger sourceSize = [edit[@"source_size"] unsignedLongLongValue];
                if (sourceSize > 64u*1024u*1024u) { *error = ChineseError(@"中文资源源文件范围异常。"); return NO; }
                [source seekToFileOffset:[edit[@"source_offset"] unsignedLongLongValue]];
                NSData *packed = [source readDataOfLength:sourceSize];
                if (packed.length != sourceSize || ![Digest(packed) isEqual:edit[@"source_sha256"]]) {
                    *error = ChineseError([NSString stringWithFormat:@"原游戏资源不匹配：%@。请检查原资源分卷。", archiveName]); return NO;
                }
                NSData *original = Inflate(packed, error);
                if (!original || original.length != [edit[@"unpacked_size"] unsignedLongLongValue]) {
                    if (!*error) *error = ChineseError(@"中文资源修改范围不匹配。"); return NO;
                }
                NSMutableData *output = [NSMutableData new];
                NSUInteger cursor = 0;
                for (NSDictionary *change in edit[@"changes"]) {
                    NSUInteger offset = [change[@"offset"] unsignedLongLongValue];
                    NSUInteger remove = [change[@"remove"] unsignedLongLongValue];
                    NSData *payload = [[NSData alloc] initWithBase64EncodedString:change[@"data"] options:0];
                    if (!payload || offset < cursor || offset > original.length || remove > original.length-offset) {
                        *error = ChineseError(@"中文资源修改范围不匹配。"); return NO;
                    }
                    [output appendData:[original subdataWithRange:NSMakeRange(cursor, offset-cursor)]];
                    [output appendData:payload];
                    cursor = offset+remove;
                }
                [output appendData:[original subdataWithRange:NSMakeRange(cursor, original.length-cursor)]];
                if (output.length != [edit[@"output_size"] unsignedLongLongValue] || ![Digest(output) isEqual:edit[@"output_sha256"]]) {
                    *error = ChineseError(@"中文资源生成校验失败。"); return NO;
                }
                NSData *compressed = [edit[@"compressed"] boolValue] ? Deflate(output, error) : output;
                if (!compressed || (![installed length] && !Backup(target, backups, name, error)) ||
                    ![files createDirectoryAtURL:target.URLByDeletingLastPathComponent withIntermediateDirectories:YES attributes:nil error:error] ||
                    ![compressed writeToURL:target options:NSDataWritingAtomic error:error]) return NO;
                ++index;
                if (progress && (index % 50 == 0 || index == edits.count))
                    progress([NSString stringWithFormat:@"正在应用中文资源：%lu / %lu", (unsigned long)index, (unsigned long)edits.count],
                             (double)index/(double)edits.count);
            }
        }
        for (NSFileHandle *source in archives.allValues) [source closeFile];
        if ((![installed length] && !Backup(fontTarget, backups, font[@"path"], error)) ||
            ![files createDirectoryAtURL:fontTarget.URLByDeletingLastPathComponent withIntermediateDirectories:YES attributes:nil error:error] ||
            ![fontData writeToURL:fontTarget options:NSDataWritingAtomic error:error] ||
            ![identity writeToURL:receipt atomically:YES encoding:NSUTF8StringEncoding error:error]) return NO;
        fprintf(stderr, "[prototype-localization] applied zh-Hans: %lu text files; native audio and scene bytes preserved\n", (unsigned long)edits.count);
        if (progress) progress(@"中文资源就绪", 1.0);
        return YES;
    } @catch (NSException *exception) {
        *error = ChineseError([NSString stringWithFormat:@"应用中文资源失败：%@", exception.reason ?: exception.name]);
        return NO;
    }
}
