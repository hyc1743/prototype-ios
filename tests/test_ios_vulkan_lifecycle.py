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

    def test_vulkan_lifecycle_runtime_is_linked_into_ios_app(self):
        source = (ROOT/'tools/build_source_ios.py').read_text(encoding='utf8')
        self.assertIn('"ios/runtime/PrototypeVulkan.c"', source)

    def test_diagnostic_candidate_uses_128_draws_and_reports_memory(self):
        source = (ROOT/'ios/source-port/PrototypeSDLMain.mm').read_text(encoding='utf8')
        self.assertIn('--proto_apple_draw_batch=128', source)
        self.assertIn('TASK_VM_INFO', source)
        self.assertIn('UIApplicationDidReceiveMemoryWarningNotification', source)


if __name__ == '__main__':
    unittest.main()
