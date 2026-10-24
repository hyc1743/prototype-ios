#pragma once

#ifdef __cplusplus
extern "C" {
#endif

// Called by UIKit lifecycle observers. The transition is idempotent and
// returns only after in-flight Vulkan queue work has drained on suspension.
void prototype_vulkan_suspend(int suspended);

#ifdef __cplusplus
}
#endif
