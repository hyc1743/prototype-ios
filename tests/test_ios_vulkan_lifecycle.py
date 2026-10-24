import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class IOSVulkanLifecycleTests(unittest.TestCase):
    def test_queue_wrappers_wait_while_backgrounded_and_transition_is_idempotent(self):
        source = (ROOT/'ios/runtime/PrototypeVulkan.c').read_text(encoding='utf8')
        self.assertIn('while (graphics_suspended || device_destroying)', source)
        self.assertIn('changed = graphics_suspended != requested;', source)
        self.assertIn('if (requested && changed && device)', source)
        self.assertIn('vkDeviceWaitIdle(device)', source)

    def test_diagnostic_candidate_uses_128_draws_and_reports_memory(self):
        source = (ROOT/'ios/source-port/PrototypeSDLMain.mm').read_text(encoding='utf8')
        self.assertIn('--proto_apple_draw_batch=128', source)
        self.assertIn('TASK_VM_INFO', source)
        self.assertIn('UIApplicationDidReceiveMemoryWarningNotification', source)

    def test_release_candidate_uses_chinese_resource_tree(self):
        source = (ROOT/'ios/source-port/PrototypeSDLMain.mm').read_text(encoding='utf8')
        self.assertIn('documents.fileSystemRepresentation) + "/game-zh"', source)
        self.assertIn('arguments.emplace_back("--game_data_root=" + localizedRoot)', source)


if __name__ == '__main__':
    unittest.main()
