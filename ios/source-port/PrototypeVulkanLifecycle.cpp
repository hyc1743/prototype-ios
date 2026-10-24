#include "PrototypeVulkanLifecycle.h"

#include <condition_variable>
#include <cstdio>
#include <mutex>

#include <rex/ui/vulkan/device.h>

namespace {
std::mutex g_mutex;
std::condition_variable g_changed;
bool g_suspended = false;
unsigned g_active_calls = 0;
bool g_installed = false;
VkDevice g_device = VK_NULL_HANDLE;
VkQueue g_last_queue = VK_NULL_HANDLE;

PFN_vkQueueSubmit g_queue_submit = nullptr;
PFN_vkQueuePresentKHR g_queue_present = nullptr;
PFN_vkAcquireNextImageKHR g_acquire_next_image = nullptr;

void EnterQueue(VkQueue queue) {
  std::unique_lock lock(g_mutex);
  g_changed.wait(lock, [] { return !g_suspended; });
  if (queue) g_last_queue = queue;
  ++g_active_calls;
}

void LeaveQueue() {
  std::lock_guard lock(g_mutex);
  --g_active_calls;
  g_changed.notify_all();
}

VkResult VKAPI_CALL GateQueueSubmit(VkQueue queue, uint32_t count,
                                    const VkSubmitInfo* submits, VkFence fence) {
  EnterQueue(queue);
  const VkResult result = g_queue_submit(queue, count, submits, fence);
  LeaveQueue();
  return result;
}

VkResult VKAPI_CALL GateQueuePresent(VkQueue queue, const VkPresentInfoKHR* present) {
  EnterQueue(queue);
  const VkResult result = g_queue_present(queue, present);
  LeaveQueue();
  return result;
}

VkResult VKAPI_CALL GateAcquireNextImage(VkDevice device, VkSwapchainKHR swapchain,
                                         uint64_t timeout, VkSemaphore semaphore, VkFence fence,
                                         uint32_t* image_index) {
  {
    std::unique_lock lock(g_mutex);
    g_changed.wait(lock, [] { return !g_suspended; });
    ++g_active_calls;
  }
  const VkResult result = g_acquire_next_image(device, swapchain, timeout, semaphore, fence,
                                                image_index);
  LeaveQueue();
  return result;
}

}  // namespace

namespace rex::ui::vulkan {

void prototype_vulkan_install_lifecycle_gate(VulkanDevice::Functions* functions,
                                              VkDevice device) {
  if (!functions) return;
  std::lock_guard lock(g_mutex);
  if (g_installed) return;
  g_queue_submit = functions->vkQueueSubmit;
  g_queue_present = functions->vkQueuePresentKHR;
  g_acquire_next_image = functions->vkAcquireNextImageKHR;
  g_device = device;
  if (!g_queue_submit || !g_queue_present || !g_acquire_next_image) {
    std::fprintf(stderr, "[prototype-vulkan] lifecycle gate unavailable: missing Vulkan function\n");
    return;
  }
  functions->vkQueueSubmit = GateQueueSubmit;
  functions->vkQueuePresentKHR = GateQueuePresent;
  functions->vkAcquireNextImageKHR = GateAcquireNextImage;
  g_installed = true;
  std::fprintf(stderr, "[prototype-vulkan] lifecycle gate installed\n");
}

}  // namespace rex::ui::vulkan

extern "C" void prototype_vulkan_suspend(int suspended) {
  const bool requested = suspended != 0;
  VkDevice device = VK_NULL_HANDLE;
  VkQueue queue = VK_NULL_HANDLE;
  {
    std::unique_lock lock(g_mutex);
    if (g_suspended == requested) return;
    g_suspended = requested;
    if (requested) {
      g_changed.wait(lock, [] { return g_active_calls == 0; });
      device = g_device;
      queue = g_last_queue;
    } else {
      g_changed.notify_all();
    }
  }
  VkResult result = VK_SUCCESS;
  if (requested) {
    if (device) result = ::vkDeviceWaitIdle(device);
    else if (queue) result = ::vkQueueWaitIdle(queue);
  }
  std::fprintf(stderr, "[prototype-vulkan] lifecycle suspended=%d installed=%d idle=%d\n",
               requested ? 1 : 0, g_installed ? 1 : 0, int(result));
}
