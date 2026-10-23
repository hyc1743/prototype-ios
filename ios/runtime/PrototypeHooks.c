/* All instruction redirects are generated before signing. Bind only data here. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "PrototypeLibnx.h"
#include "PrototypeVulkan.h"
#include "PrototypeHookTable.h"
extern void *front_metal_layer(void);
void prototype_bind_guest_hooks(uint8_t *base, size_t size) {
    if (!base || size < PROTOTYPE_TABLE_OFFSET + PROTOTYPE_TABLE_SIZE ||
        memcmp(base + 0x10, "NRO0", 4) ||
        memcmp(base + 0x40, prototype_expected_build, sizeof prototype_expected_build)) abort();
    prototype_vk_set_metal_layer(front_metal_layer());
    uintptr_t *table = (uintptr_t *)(base + PROTOTYPE_TABLE_OFFSET);
    memset(table, 0, PROTOTYPE_TABLE_SIZE);
    for (size_t i = 0; i < PROTOTYPE_HOOK_COUNT; ++i) {
        const PrototypeHookBinding *hook = &prototype_hook_bindings[i];
        uintptr_t target = hook->slot < 1024 ? (uintptr_t)prototype_vk_resolve(hook->symbol)
                                          : (uintptr_t)prototype_libnx_resolve(hook->symbol);
        if (!target || hook->slot >= PROTOTYPE_TABLE_SIZE / sizeof(*table)) {
            fprintf(stderr, "[prototype] unresolved native hook %s\n", hook->symbol); abort();
        }
        table[hook->slot] = target;
    }
    fprintf(stderr, "[prototype] bound %zu native hooks\n", (size_t)PROTOTYPE_HOOK_COUNT);
}
