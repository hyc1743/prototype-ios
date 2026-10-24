import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VulkanLifecycleGateTests(unittest.TestCase):
    def test_gate_wraps_submit_present_acquire_and_idle(self):
        source = (ROOT / 'ios/source-port/PrototypeVulkanLifecycle.cpp').read_text(encoding='utf8')
        for name in ('vkQueueSubmit', 'vkQueuePresentKHR', 'vkAcquireNextImageKHR',
                     'vkQueueWaitIdle', 'vkDeviceWaitIdle'):
            self.assertIn(name, source)
        self.assertIn('g_changed.wait(lock, [] { return !g_suspended; });', source)
        self.assertIn('g_changed.wait(lock, [] { return g_active_calls == 0; });', source)

    def test_sdk_installs_gate_after_loading_device_functions(self):
        source = (ROOT / 'tools/prepare_source_port.py').read_text(encoding='utf8')
        self.assertIn('src/ui/vulkan/vulkan_device.cpp', source)
        self.assertIn('prototype_vulkan_install_lifecycle_gate(&dfn, device->device_)', source)

    def test_uikit_observers_suspend_before_background_and_resume_active(self):
        source = (ROOT / 'ios/source-port/PrototypeSDLMain.mm').read_text(encoding='utf8')
        self.assertIn('UIApplicationWillResignActiveNotification', source)
        self.assertIn('UIApplicationDidBecomeActiveNotification', source)
        self.assertIn('prototype_vulkan_suspend(1)', source)
        self.assertIn('prototype_vulkan_suspend(0)', source)


if __name__ == '__main__':
    unittest.main()
