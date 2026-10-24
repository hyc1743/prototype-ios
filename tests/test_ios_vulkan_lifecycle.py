import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class IOSVulkanLifecycleTests(unittest.TestCase):
    def test_uikit_suspend_events_gate_vulkan_before_touch_state_changes(self):
        source = (ROOT/'ios/source-port/PrototypeNativePad.m').read_text(encoding='utf8')
        method_start = source.index('- (void)setSuspended:(BOOL)suspended {')
        method_end = source.index('\n- (void)close {', method_start)
        method = source[method_start:method_end]
        gate = method.index('prototype_vk_suspend(suspended);')
        self.assertLess(gate, method.index('SDL_LockJoysticks();'))
        for notification in ('UIApplicationWillResignActiveNotification',
                             'UIApplicationDidEnterBackgroundNotification',
                             'UIApplicationDidBecomeActiveNotification'):
            self.assertIn(notification, source)

    def test_queue_wrappers_wait_while_backgrounded_and_transition_is_idempotent(self):
        source = (ROOT/'ios/runtime/PrototypeVulkan.c').read_text(encoding='utf8')
        self.assertIn('while (graphics_suspended || device_destroying)', source)
        self.assertIn('changed = graphics_suspended != requested;', source)
        self.assertIn('if (requested && changed && device)', source)
        self.assertIn('vkDeviceWaitIdle(device)', source)


if __name__ == '__main__':
    unittest.main()
