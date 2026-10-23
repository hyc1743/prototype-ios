#pragma once
#include <stddef.h>
#include <stdint.h>
int prototype_libnx_boot(uint8_t *, size_t, uint32_t, int, char **);
void prototype_libnx_app_init(void);
void *prototype_libnx_resolve(const char *);
void prototype_set_error_callback(void (*callback)(const char *));
