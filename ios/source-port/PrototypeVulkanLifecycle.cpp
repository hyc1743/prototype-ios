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
PFN_vkQueueWaitIdle g_queue_wait_idle = nullptr;
PFN_vkDeviceWaitIdle g_device_wait_idle = nullptr;

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

VkResult VKAPI_CALL GateQueueWaitIdle(VkQueue queue) {
  EnterQueue(queue);
  const VkResult result = g_queue_wait_idle(queue);
  LeaveQueue();
  return result;
}

VkResult VKAPI_CALL GateDeviceWaitIdle(VkDevice device) {
  {
    std::unique_lock lock(g_mutex);
    g_changed.wait(lock, [] { return !g_suspended; });
    ++g_active_calls;
  }
  const VkResult result = g_device_wait_idle(device);
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
  g_queue_wait_idle = functions->vkQueueWaitIdle;
  g_device_wait_idle = functions->vkDeviceWaitIdle;
  g_device = device;
  if (!g_queue_submit || !g_queue_present || !g_acquire_next_image || !g_queue_wait_idle ||
      !g_device_wait_idle) {
    std::fprintf(stderr, "[prototype-vulkan] lifecycle gate unavailable: missing Vulkan function\n");
    return;
  }
  functions->vkQueueSubmit = GateQueueSubmit;
  functions->vkQueuePresentKHR = GateQueuePresent;
  functions->vkAcquireNextImageKHR = GateAcquireNextImage;
  functions->vkQueueWaitIdle = GateQueueWaitIdle;
  functions->vkDeviceWaitIdle = GateDeviceWaitIdle;
  g_installed = true;
  std::fprintf(stderr, "[prototype-vulkan] lifecycle gate installed\n");
}

}  // namespace rex::ui::vulkan

extern "C" void prototype_vulkan_suspend(int suspended) {
  const bool requested = suspended != 0;
  PFN_vkDeviceWaitIdle wait_idle = nullptr;
  PFN_vkQueueWaitIdle queue_wait_idle = nullptr;
  VkDevice device = VK_NULL_HANDLE;
  VkQueue queue = VK_NULL_HANDLE;
  {
    std::unique_lock lock(g_mutex);
    if (g_suspended == requested) return;
    g_suspended = requested;
    if (requested) {
      g_changed.wait(lock, [] { return g_active_calls == 0; });
      wait_idle = g_device_wait_idle;
      queue_wait_idle = g_queue_wait_idle;
      device = g_device;
      queue = g_last_queue;
    } else {
      g_changed.notify_all();
    }
  }
  VkResult result = VK_SUCCESS;
  if (requested) {
    if (wait_idle && device) result = wait_idle(device);
    else if (queue_wait_idle && queue) result = queue_wait_idle(queue);
  }
  std::fprintf(stderr, "[prototype-vulkan] lifecycle suspended=%d installed=%d idle=%d\n",
               requested ? 1 : 0, g_installed ? 1 : 0, int(result));
}
