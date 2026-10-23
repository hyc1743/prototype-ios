import io
import ast
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from prepare_source_port import apple_fiber_source, apple_memory_source, apple_input_source, extract_archive, generated_sources, replace_once, source_sha


MEMORY_QUERY_FIXTURE = '''#include <cstddef>
#include <cstdint>
#if REX_PLATFORM_MAC
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach/vm_region.h>
#endif
enum class PageAccess { kExecuteReadWrite, kExecuteReadOnly, kReadWrite, kReadOnly, kNoAccess };
bool QueryProtect(void* base_address, size_t& length, PageAccess& access_out) {
  mach_vm_address_t address = reinterpret_cast<mach_vm_address_t>(base_address);
  mach_vm_size_t region_size = 0;
  vm_region_basic_info_data_64_t info;
  mach_msg_type_number_t info_count = VM_REGION_BASIC_INFO_COUNT_64;
  mach_port_t object_name;

  kern_return_t kr =
      mach_vm_region(mach_task_self(), &address, &region_size, VM_REGION_BASIC_INFO_64,
                     reinterpret_cast<vm_region_info_t>(&info), &info_count, &object_name);
  if (kr != KERN_SUCCESS) {
    return false;
  }
  if (address > reinterpret_cast<mach_vm_address_t>(base_address)) {
    return false;
  }

  length = static_cast<size_t>((address + region_size) -
                               reinterpret_cast<mach_vm_address_t>(base_address));
  if ((info.protection & (VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE)) ==
      (VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE)) {
    access_out = PageAccess::kExecuteReadWrite;
  } else if ((info.protection & (VM_PROT_READ | VM_PROT_EXECUTE)) ==
             (VM_PROT_READ | VM_PROT_EXECUTE)) {
    access_out = PageAccess::kExecuteReadOnly;
  } else if ((info.protection & (VM_PROT_READ | VM_PROT_WRITE)) == (VM_PROT_READ | VM_PROT_WRITE)) {
    access_out = PageAccess::kReadWrite;
  } else if (info.protection & VM_PROT_READ) {
    access_out = PageAccess::kReadOnly;
  } else {
    access_out = PageAccess::kNoAccess;
  }
  return true;
}
'''


class SourcePreparationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("clang++") or shutil.which("g++"), "native C++ compiler unavailable")
    def test_preconnected_and_hotplug_snapshot_share_one_device(self):
        fixture = '''#include <vector>
#include <optional>
#include <cassert>
using SDL_JoystickID = unsigned;
constexpr unsigned SDL_EVENT_GAMEPAD_ADDED=1;
struct SDL_Event { unsigned type{}; struct { SDL_JoystickID which{}; } gdevice; };
static std::vector<SDL_JoystickID> connected;
static unsigned freed=0;
SDL_JoystickID* SDL_GetGamepads(int* count) {
  *count=int(connected.size());
  return connected.empty() ? nullptr : connected.data();
}
void SDL_free(void*) { ++freed; }
class SDLInputDriver {
 public:
  bool SDL_Gamepad_initialized_=false;
  std::vector<SDL_Event> pending;
  std::vector<SDL_JoystickID> devices;
  void Start() {
      SDL_Gamepad_initialized_ = true;
  }
  void HandleEvent(SDL_Event event) { pending.push_back(event); }
  std::optional<unsigned> GetControllerIndexFromInstanceID(SDL_JoystickID id) {
    for(unsigned i=0;i<devices.size();++i) if(devices[i]==id) return i;
    return {};
  }
  void OnControllerDeviceAddedLocked(const SDL_Event& event);
  void Drain() { for(auto event:pending) OnControllerDeviceAddedLocked(event); pending.clear(); }
};
void SDLInputDriver::OnControllerDeviceAddedLocked(const SDL_Event& event) {
  devices.push_back(event.gdevice.which);
}
int main() {
  SDLInputDriver empty;
  empty.Start(); empty.Drain(); assert(empty.devices.empty() && freed==0);
  connected={17,42};
  SDLInputDriver driver;
  driver.HandleEvent({SDL_EVENT_GAMEPAD_ADDED,{17}});
  driver.Start();
  driver.HandleEvent({SDL_EVENT_GAMEPAD_ADDED,{42}});
  driver.Drain();
  assert(driver.devices==connected && freed==1);
  driver.HandleEvent({SDL_EVENT_GAMEPAD_ADDED,{99}}); driver.Drain();
  assert((driver.devices==std::vector<unsigned>{17,42,99}));
  driver.devices.erase(driver.devices.begin());
  driver.HandleEvent({SDL_EVENT_GAMEPAD_ADDED,{17}}); driver.Drain();
  assert((driver.devices==std::vector<unsigned>{42,99,17}));
}
'''
        compiler = shutil.which("clang++") or shutil.which("g++")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, executable = root / "input.cpp", root / ("input.exe" if sys.platform == "win32" else "input")
            source.write_text(apple_input_source(fixture))
            subprocess.run([compiler, "-std=c++17", str(source), "-o", str(executable)], check=True, capture_output=True)
            subprocess.run([str(executable)], check=True, capture_output=True)

    def test_ios_region_overlay_preserves_permission_decoder_and_refuses_changed_anchors(self):
        patched = apple_memory_source(MEMORY_QUERY_FIXTURE)
        permissions = MEMORY_QUERY_FIXTURE[MEMORY_QUERY_FIXTURE.index("  if ((info.protection"):]
        self.assertEqual(patched[patched.index("  if ((info.protection"):], permissions)
        self.assertIn("vm_region_64(mach_task_self(), &address, &region_size, VM_REGION_BASIC_INFO_64", patched)
        self.assertIn("using RegionAddress = vm_address_t;", patched)
        self.assertIn("using RegionSize = vm_size_t;", patched)
        self.assertIn("using RegionAddress = mach_vm_address_t;", patched)
        self.assertLess(patched.index("mach_port_deallocate("), patched.index("if (kr != KERN_SUCCESS)"))
        for changed in (MEMORY_QUERY_FIXTURE.replace("#include <mach/mach_vm.h>", ""),
                        MEMORY_QUERY_FIXTURE.replace("mach_port_t object_name;", "mach_port_t different;")):
            with self.assertRaises(ValueError):
                apple_memory_source(changed)

    @unittest.skipUnless(shutil.which("clang++") or shutil.which("g++"), "native C++ compiler unavailable; target iOS compile is a separate CI gate")
    def test_public_and_desktop_region_apis_keep_bounds_permissions_and_port_lifetime(self):
        compiler = shutil.which("clang++") or shutil.which("g++")
        public_header = '''#pragma once
#include <cstdint>
#include <cassert>
using vm_map_read_t = unsigned;
using vm_address_t = uintptr_t;
using vm_size_t = uintptr_t;
using mach_msg_type_number_t = unsigned;
using mach_port_t = unsigned;
using kern_return_t = int;
using vm_region_info_t = int*;
struct vm_region_basic_info_data_64_t { int protection; };
constexpr int VM_REGION_BASIC_INFO_COUNT_64=9, VM_REGION_BASIC_INFO_64=9;
constexpr int VM_PROT_READ=1, VM_PROT_WRITE=2, VM_PROT_EXECUTE=4, KERN_SUCCESS=0;
constexpr mach_port_t MACH_PORT_NULL=0;
uintptr_t next_address=0;
uintptr_t next_size=0;
int next_protection=0, next_result=0, released=0, public_calls=0, desktop_calls=0;
mach_port_t next_object=77;
inline mach_port_t mach_task_self() { return 1; }
inline kern_return_t mach_port_deallocate(mach_port_t task, mach_port_t object) {
  assert(task==1 && object==77); ++released; return 0;
}
inline kern_return_t region_common(vm_map_read_t task, uintptr_t* address, uintptr_t* size,
  int flavor, vm_region_info_t info, mach_msg_type_number_t* count, mach_port_t* object) {
  assert(task==1 && flavor==VM_REGION_BASIC_INFO_64 && *count==VM_REGION_BASIC_INFO_COUNT_64);
  *address=next_address; *size=next_size; *info=next_protection; *object=next_object; return next_result;
}
inline kern_return_t vm_region_64(vm_map_read_t task, vm_address_t* address, vm_size_t* size,
  int flavor, vm_region_info_t info, mach_msg_type_number_t* count, mach_port_t* object) {
  ++public_calls; return region_common(task,address,size,flavor,info,count,object);
}
'''
        desktop_header = '''#pragma once
#if REX_PLATFORM_IOS
#error mach_vm.h unsupported in the iOS API fixture
#endif
using mach_vm_address_t = uintptr_t;
using mach_vm_size_t = uintptr_t;
inline kern_return_t mach_vm_region(vm_map_read_t task, mach_vm_address_t* address, mach_vm_size_t* size,
  int flavor, vm_region_info_t info, mach_msg_type_number_t* count, mach_port_t* object) {
  ++desktop_calls; return region_common(task,address,size,flavor,info,count,object);
}
'''
        driver = '''
int main() {
  PageAccess access=PageAccess::kNoAccess;
  size_t length=999;
  const int protections[]={0,1,3,5,7};
  const PageAccess expected[]={PageAccess::kNoAccess,PageAccess::kReadOnly,PageAccess::kReadWrite,
    PageAccess::kExecuteReadOnly,PageAccess::kExecuteReadWrite};
  next_address=0x1000; next_size=0x4000;
  for(int i=0;i<5;++i) {
    next_protection=protections[i];
    assert(QueryProtect(reinterpret_cast<void*>(uintptr_t(0x1800)),length,access));
    assert(length==0x3800 && access==expected[i]);
  }
  assert(released==5);
  next_address=0x2000;
  assert(!QueryProtect(reinterpret_cast<void*>(uintptr_t(0x1800)),length,access));
  next_address=0x1000; next_size=0x800;
  assert(!QueryProtect(reinterpret_cast<void*>(uintptr_t(0x1800)),length,access));
  next_size=0;
  assert(!QueryProtect(reinterpret_cast<void*>(uintptr_t(0x1000)),length,access));
  next_address=UINTPTR_MAX-0x3fff; next_size=0x4000;
  assert(QueryProtect(reinterpret_cast<void*>(UINTPTR_MAX-3),length,access));
  assert(length==4);
  next_result=5;
  assert(!QueryProtect(reinterpret_cast<void*>(uintptr_t(0x1800)),length,access));
  assert(released==10);
  next_result=0; next_address=0x1000; next_size=0x4000; next_object=MACH_PORT_NULL;
  assert(QueryProtect(reinterpret_cast<void*>(uintptr_t(0x1800)),length,access));
  assert(released==10);
#if REX_PLATFORM_IOS
  assert(public_calls==11 && desktop_calls==0);
#else
  assert(desktop_calls==11 && public_calls==0);
#endif
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            include = root / "mach"
            include.mkdir()
            (include / "mach.h").write_text(public_header)
            (include / "mach_vm.h").write_text(desktop_header)
            (include / "vm_region.h").write_text("#pragma once\n")
            source = root / "region.cpp"
            source.write_text(apple_memory_source(MEMORY_QUERY_FIXTURE) + driver)
            for ios in (0, 1):
                with self.subTest(ios=ios):
                    executable = root / ("region-" + str(ios) + (".exe" if sys.platform == "win32" else ""))
                    subprocess.run([compiler, "-std=c++17", "-I", str(root), "-DREX_PLATFORM_MAC=1",
                                    "-DREX_PLATFORM_IOS=" + str(ios), str(source), "-o", str(executable)],
                                   check=True, capture_output=True)
                    subprocess.run([str(executable)], check=True, capture_output=True)

    def test_fiber_macho_adaptation_keeps_assembly_statement_complete(self):
        source = ('#if REX_PLATFORM_SWITCH\n__asm__(\n'
                  '    ".global rex_fiber_swap\\n"\n'
                  '    ".type rex_fiber_swap, %function\\n"\n'
                  '    "rex_fiber_swap:\\n"\n'
                  '    "    ret\\n"\n'
                  '    ".global rex_fiber_entry\\n"\n'
                  '    "rex_fiber_entry:\\n"\n'
                  '    "    b   rex_fiber_trampoline\\n"\n'
                  '    ".size rex_fiber_entry, .-rex_fiber_entry\\n");\n#endif\n')
        result = apple_fiber_source(source)
        statement = re.search(r"__asm__\((.*?)\);", result, re.DOTALL)
        self.assertIsNotNone(statement)
        literals = re.findall(r'"(?:[^"\\]|\\.)*"', statement.group(1))
        assembly = "".join(ast.literal_eval(value) for value in literals)
        self.assertIn(".global _rex_fiber_swap\n_rex_fiber_swap:", assembly)
        self.assertIn("b   _rex_fiber_trampoline", assembly)
        self.assertNotIn(".type", assembly)
        self.assertNotIn(".size", assembly)

    def test_archive_cannot_write_outside_the_prepared_tree(self):
        for name in ("../escaped.h", "/absolute.h", "sdk/../../escaped.h"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                payload = io.BytesIO()
                with tarfile.open(fileobj=payload, mode="w") as archive:
                    member = tarfile.TarInfo(name)
                    member.size = 4
                    archive.addfile(member, io.BytesIO(b"test"))
                with self.assertRaises(ValueError):
                    extract_archive(payload.getvalue(), Path(directory) / "prepared")
                self.assertFalse((Path(directory) / "escaped.h").exists())

    def test_dependency_population_preserves_fork_overlays(self):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w") as archive:
            for name in ("config.h", "new.h"):
                member = tarfile.TarInfo(name)
                member.size = 6
                archive.addfile(member, io.BytesIO(b"vendor"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.h").write_bytes(b"reviewed fork overlay")
            extract_archive(payload.getvalue(), root, only_missing=True)
            self.assertEqual((root / "config.h").read_bytes(), b"reviewed fork overlay")
            self.assertEqual((root / "new.h").read_bytes(), b"vendor")

    def generated_fixture(self, root):
        root.mkdir()
        for name in ("prototype_init.cpp", "prototype_init.h", "prototype_register.cpp", "prototype_recomp.0.cpp"):
            (root / name).write_text("// source fixture\n")
        (root / "sources.cmake").write_text("set(GENERATED_SOURCES ${CMAKE_CURRENT_LIST_DIR}/prototype_recomp.0.cpp)\n")
        (root / "prototype_pch.h").write_text(
            "#if REX_PLATFORM_MAC && REX_ARCH_ARM64\n#define REX_PHYS_HOST_OFFSET(a) 0x1000u\n#endif\n")
        return root

    def test_missing_game_code_and_source_list_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.generated_fixture(Path(directory) / "generated")
            self.assertEqual(generated_sources(root), ["prototype_recomp.0.cpp"])
            (root / "prototype_recomp.0.cpp").unlink()
            with self.assertRaisesRegex(ValueError, "missing unit"):
                generated_sources(root)
            outside = root.parent / "prototype_recomp.outside.cpp"
            outside.write_text("// outside\n")
            (root / "prototype_recomp.0.cpp").write_text("// fixture\n")
            (root / "sources.cmake").write_text("${CMAKE_CURRENT_LIST_DIR}/prototype_recomp.0.cpp\n"
                                               "${CMAKE_CURRENT_LIST_DIR}/../prototype_recomp.outside.cpp\n")
            with self.assertRaisesRegex(ValueError, "escapes"):
                generated_sources(root)

    def test_internal_vendor_links_are_materialized_but_external_links_are_rejected(self):
        for linked in ("original.h", "../outside.h"):
            with self.subTest(linked=linked), tempfile.TemporaryDirectory() as directory:
                payload = io.BytesIO()
                with tarfile.open(fileobj=payload, mode="w") as archive:
                    original = tarfile.TarInfo("original.h")
                    original.size = 4
                    archive.addfile(original, io.BytesIO(b"real"))
                    member = tarfile.TarInfo("alias.h")
                    member.type = tarfile.SYMTYPE
                    member.linkname = linked
                    archive.addfile(member)
                root = Path(directory)
                if linked.startswith(".."):
                    with self.assertRaises(ValueError):
                        extract_archive(payload.getvalue(), root)
                else:
                    extract_archive(payload.getvalue(), root)
                    self.assertEqual((root / "alias.h").read_bytes(), b"real")
                    self.assertFalse((root / "alias.h").is_symlink())

    def test_unadjusted_switch_translation_and_original_binary_inputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.generated_fixture(Path(directory) / "generated")
            (root / "default.xex").write_bytes(b"original resource fixture")
            with self.assertRaisesRegex(ValueError, "non-source"):
                generated_sources(root)
            (root / "default.xex").unlink()
            (root / "prototype_pch.h").write_text("#define REX_PHYS_HOST_OFFSET(a) 0u\n")
            with self.assertRaisesRegex(ValueError, "Apple E0"):
                generated_sources(root)

    def test_reviewed_patch_identity_is_independent_of_checkout_line_endings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "lf").write_bytes(b"first\nsecond\n")
            (root / "crlf").write_bytes(b"first\r\nsecond\r\n")
            self.assertEqual(source_sha(root / "lf"), source_sha(root / "crlf"))
            (root / "crlf").write_bytes(b"first\r\nchanged\r\n")
            self.assertNotEqual(source_sha(root / "lf"), source_sha(root / "crlf"))
        with self.assertRaises(ValueError):
            replace_once("two anchors two anchors", "anchors", "replacement")
        with self.assertRaises(ValueError):
            replace_once("missing", "anchors", "replacement")


if __name__ == "__main__":
    unittest.main()
