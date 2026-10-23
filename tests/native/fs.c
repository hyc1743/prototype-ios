#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include "PrototypeFS.c"

static void put(const char *root, const char *leaf, const char *value) {
    char path[PATH_MAX];
    assert(snprintf(path, sizeof path, "%s/%s", root, leaf) < (int)sizeof path);
    FILE *stream = fopen(path, "wb"); assert(stream);
    assert(fwrite(value, 1, strlen(value), stream) == strlen(value));
    assert(fclose(stream) == 0);
}
static void read_exact(const GuestDevice *dev, const char *path, const char *expected) {
    int reent = 0, file = -1;
    char data[64] = {0};
    assert(dev->open(&reent, &file, path, 0, 0) == 0);
    assert(dev->read(&reent, &file, data, sizeof data) == (ssize_t)strlen(expected));
    assert(!memcmp(data, expected, strlen(expected)));
    assert(dev->close(&reent, &file) == 0);
}
int main(int argc, char **argv) {
    assert(argc == 2);
    char game[PATH_MAX], saves[PATH_MAX];
    assert(snprintf(game, sizeof game, "%s/game", argv[1]) < (int)sizeof game);
    assert(snprintf(saves, sizeof saves, "%s/saves", argv[1]) < (int)sizeof saves);
    assert(setenv("PROTOTYPE_GAME_ROOT", game, 1) == 0);
    assert(setenv("PROTOTYPE_SAVE_ROOT", saves, 1) == 0);
    assert(setenv("PROTOTYPE_DOCUMENTS_ROOT", argv[1], 1) == 0);
    assert(chdir(argv[1]) == 0);
    put(game, "default.xex", "XEX12");
    put(argv[1], "prototype.toml", "renderer=\"native\"");

    uint8_t *base = calloc(1, 64u << 20); assert(base);
    prototype_fs_install(base);
    const GuestDevice **list = (const GuestDevice **)(base + 0x3d9dfb0);
    assert(list[0] == NULL && list[1] && list[2] && list[3]);
    assert(list[1] == list[2] && !strcmp(list[1]->name, "ioslog"));
    const GuestDevice *dev = list[prototype_find_device("sdmc:/switch/prototype-nx")];
    assert(!strcmp(dev->name, "ios") && dev->file_size == sizeof(int));
    assert(offsetof(GuestDevice, device_data) == 0xb0);
    puts("FS_PASS original_devoptab_layout_and_offset");

    read_exact(dev, "romfs:/default.xex", "XEX12");
    read_exact(dev, "sdmc:/switch/prototype-nx/game/default.xex", "XEX12");
    read_exact(dev, "/switch/prototype-nx/game/default.xex", "XEX12");
    read_exact(dev, "game/default.xex", "XEX12");
    read_exact(dev, "sdmc:/switch/prototype-nx/prototype.toml", "renderer=\"native\"");
    puts("FS_PASS original_game_and_config_paths");

    int reent = 0, file = -1;
    uint64_t guarded[17]; memset(guarded, 0xa5, sizeof guarded);
    GuestStat *info = (GuestStat *)((uint8_t *)guarded + 16);
    assert(dev->stat(&reent, "romfs:/default.xex", info) == 0);
    assert(info->size == 5 && S_ISREG(info->mode));
    assert(info->mtim.sec > 0 && info->blksize > 0);
    const uint8_t *guard = (const uint8_t *)guarded;
    for (size_t i = 0; i < 16; i++) assert(guard[i] == 0xa5 && guard[120 + i] == 0xa5);
    assert(sizeof *info == 104 && offsetof(GuestStat, size) == 16);
    puts("FS_PASS original_104_byte_stat_abi");

    /* Newlib flag values deliberately differ from Linux and Darwin values. */
    assert(dev->open(&reent, &file, "save:/prototype/test.bin", 2 | 0x200 | 0x400 | 0x800, 0600) == 0);
    assert(dev->write(&reent, &file, "ABCDE", 5) == 5);
    assert(dev->sync(&reent, &file) == 0);
    assert(dev->seek(&reent, &file, 1, SEEK_SET) == 1);
    char byte = 0; assert(dev->read(&reent, &file, &byte, 1) == 1 && byte == 'B');
    assert(dev->fstat(&reent, &file, info) == 0 && info->size == 5);
    assert(dev->truncate(&reent, &file, 2) == 0);
    assert(dev->close(&reent, &file) == 0);
    int other = -1;
    assert(dev->open(&reent, &other, "save:/prototype/test.bin", 1 | 0x200 | 0x800, 0600) == -1 && reent == EEXIST);
    assert(dev->open(&reent, &file, "save:/prototype/test.bin", 1 | 0x8, 0600) == 0);
    assert(dev->write(&reent, &file, "Z", 1) == 1 && dev->close(&reent, &file) == 0);
    read_exact(dev, "save:/test.bin", "ABZ");
    read_exact(dev, "save:/prototype/test.bin", "ABZ");
    assert(dev->mkdir(&reent, "save:/prototype/slots", 0700) == 0);
    assert(dev->rename(&reent, "save:/prototype/test.bin", "save:/prototype/slots/moved.bin") == 0);
    read_exact(dev, "save:/slots/moved.bin", "ABZ");
    assert(dev->unlink(&reent, "save:/slots/moved.bin") == 0);
    puts("FS_PASS native_save_flags_seek_stat_sync_and_rename");

    GuestDir directory = {.device = 3, .state = calloc(1, dev->dir_size)}; assert(directory.state);
    assert(dev->diropen(&reent, &directory, "romfs:/") == &directory);
    char name[256];
    assert(dev->dirnext(&reent, &directory, name, info) == 0 && !strcmp(name, "default.xex") && info->size == 5);
    assert(dev->dirnext(&reent, &directory, name, info) == -1 && reent == ENOENT);
    assert(dev->dirreset(&reent, &directory) == 0);
    assert(dev->dirnext(&reent, &directory, name, info) == 0 && !strcmp(name, "default.xex"));
    assert(dev->dirclose(&reent, &directory) == 0); free(directory.state);
    puts("FS_PASS native_directory_abi");

    assert(dev->stat(&reent, "unknown:/default.xex", info) == -1 && reent == ENOENT);
    char too_long[PATH_MAX + 20]; memset(too_long, 'x', sizeof too_long - 1); too_long[sizeof too_long - 1] = 0;
    assert(dev->stat(&reent, too_long, info) == -1 && reent == 91);
    free(base);
    puts("FS_ALL_PASSED");
    return 0;
}
