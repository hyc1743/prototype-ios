#!/usr/bin/env python3
"""Materialize a pinned original-source Prototype Apple build without game data.

Produces a private build workspace and reviewable overlays; never modifies the
reference repositories, runs their build scripts, or claims a compiled result.
"""
from __future__ import annotations

import argparse
import configparser
import difflib
import hashlib
import io
import json
import posixpath
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
SDK_REPOSITORY = "https://github.com/nebadasSwifty/masseffect-nx.git"
SDK_COMMIT = "a85b0b419ec3e332165ae1617c980bd399856a44"
DEPENDENCY_REPOSITORY = "https://github.com/rexglue/rexglue-sdk.git"
DEPENDENCY_COMMIT = "c94f5ebdcb3c9d1a460ca48e04f9758448f8d518"
PROTOTYPE_COMMIT = "ea65b2e188c62be33fe00e566288e61914e4a78e"
PATCH_COMMIT = "292de4b382dbe335011d8b4f56ec4212df5ef74c"
PATCH_SHA256 = "a7ac79c90273ed4b17233f8f88381183270f7f232b47aa134c651e4ae4677bcc"
CODEGEN_PATCH_SHA256 = "f9e06d2fc0e34db6805bdd6612bc8a00d5577834fc001f822634a2076bcd04d1"
PATCHED_SDK_INPUTS = {
    "src/core/exception_handler_posix.cpp": "c615444b11c59377536e0616aff93b4500bc0052de47b74208f4d36a673b46ac",
    "src/graphics/pipeline/shader/spirv_translator.cpp": "770a6383a78ea395bcb6901281639dccb09bfe1554033689ea067be1bb6dc524",
    "src/audio/sdl/sdl_audio_driver.cpp": "1e9a0d9b459ee089bb53ff3529d0f70fde1e5659b79fbd0ae2aa759ed3610186",
    "src/input/sdl/sdl_input_driver.cpp": "5317f106ddd16cbf68b590061dba188a8090b05238f460e25f24eec27840a30c",
    "CMakeLists.txt": "c6bef64cd480a56f3c090663b9e8a57698ea69ac62cf84e0a95b48d981e24557",
    "src/system/CMakeLists.txt": "132476ae77986b2c08dab266db787dc00588820ba3f339d82feadec96d6106ee",
    "src/graphics/CMakeLists.txt": "5b4d9b4dfbf79185f9a7057866e17c3f569fe515f09e19b1bd98b61fdc0dd24a",
    "src/ui/vulkan/vulkan_device.cpp": "1ca6b8741631cb3d6fbc8b6ff9395cf5d0e372c1885ce34d9728fc7b419c5f59",
    "thirdparty/CMakeLists.txt": "0c36fce05247ebb39bce69a1c2f2e85cd2056ab80c9dd7573f79de37eecc5b0e",
    "src/system/gpu_plugin_loader.cpp": "a1cb457329921ac316c18ec0ea8943284b23ae3e267e061aefd021cc54be5ee9",
    "src/ui/vulkan/vulkan_instance.cpp": "c1d79ade03e1925f96e47f0eddeafa9919d22708c44ce84bf5cfb6935fc8c4e7",
    "src/ui/vulkan/spirv_tools_context.cpp": "9495725859038018ea218d69ea9316587f033492e1dacfd9d8e65e80c67c6b68",
    "include/rex/thread/fiber.h": "3c865848d8e2a6109ebf0b8d74c61e161fda1f90737a0015c8c9becf4b60db39",
    "src/core/CMakeLists.txt": "c826aca17fa959bd91c102c99722155ab0cda89d93d4d7ceb2c3d4d3ab75274b",
    "src/core/fiber_switch.cpp": "4021a251bb614dacff564f7afb6426c3acc14baabefc9e022060793a662f7b4f",
    "src/core/memory_posix.cpp": "05d2f6b174dc981b361febb56d6441b0a3b3bd6670411a9569d5606d8e4817b2",
    "src/ui/windowed_app_main_sdl.cpp": "49c1fe205eb4802967896791fe8883c10daad0a7bc0efa5f8e60ec4ecc4aabf7",
}


def sha(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def source_sha(path):
    """Canonical source identity independent of Git's checkout EOL setting."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def command(args, *, cwd=None, binary=False):
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout if binary else result.stdout.decode("utf8").strip()


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("source overlay anchor changed: " + old[:100])
    return text.replace(old, new, 1)


def git_cache(cache, repository, commit, offline=False):
    """Fetch an exact object into an isolated bare cache; no checkout hooks."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not repository.startswith("https://github.com/"):
        raise ValueError("dependency must use a full Git object and an HTTPS GitHub source")
    destination = cache / (hashlib.sha256(repository.encode()).hexdigest()[:16] + ".git")
    if not destination.exists():
        if offline:
            raise ValueError("pinned Git cache missing: " + repository)
        command(["git", "init", "--bare", destination])
    try:
        found = command(["git", "--git-dir", destination, "rev-parse", "--verify", commit + "^{commit}"])
    except subprocess.CalledProcessError:
        if offline:
            raise ValueError("pinned Git object missing: " + commit) from None
        command(["git", "--git-dir", destination, "fetch", "--depth=1", repository, commit])
        found = command(["git", "--git-dir", destination, "rev-parse", "--verify", commit + "^{commit}"])
    if found != commit:
        raise ValueError("Git object does not match the pinned commit")
    return destination


def extract_archive(raw, destination, *, only_missing=False):
    """Reject escapes and materialize internal links without OS symlinks."""
    destination = destination.resolve()
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        members = {member.name.rstrip("/"): member for member in archive.getmembers()}
        links = []
        for member in archive.getmembers():
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("unsafe member in pinned source archive: " + member.name)
            target = destination.joinpath(*relative.parts)
            if not target.resolve().is_relative_to(destination):
                raise ValueError("source archive escapes destination")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.issym() or member.islnk():
                links.append(member)
            elif member.isfile() and not (only_missing and target.exists()):
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
            elif not member.isfile():
                raise ValueError("unsupported source archive member: " + member.name)

        def resolve_link(member, seen):
            if member.name in seen:
                raise ValueError("cyclic link in source archive")
            if not (member.issym() or member.islnk()):
                return member
            base = str(PurePosixPath(member.name).parent) if member.issym() else "."
            linked = posixpath.normpath(posixpath.join(base, member.linkname))
            if PurePosixPath(linked).is_absolute() or linked == ".." or linked.startswith("../") or linked not in members:
                raise ValueError("source archive link escapes or has no target: " + member.name)
            return resolve_link(members[linked], seen | {member.name})

        for member in links:
            referent = resolve_link(member, set())
            source = destination.joinpath(*PurePosixPath(referent.name).parts)
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if only_missing and target.exists():
                continue
            if not source.resolve().is_relative_to(destination):
                raise ValueError("source archive link escapes destination")
            if referent.isdir():
                if target.is_relative_to(source) or source.is_relative_to(target):
                    raise ValueError("recursive directory link in source archive")
                files = [path for path in source.rglob("*") if path.is_file()]
                for path in files:
                    copied = target / path.relative_to(source)
                    copied.parent.mkdir(parents=True, exist_ok=True)
                    if not (only_missing and copied.exists()):
                        shutil.copyfile(path, copied)
            elif referent.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            else:
                raise ValueError("source archive link has unsupported target")


def archive_tree(cache, commit, destination, paths=None, only_missing=False):
    args = ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "--git-dir", cache, "archive", commit]
    if paths:
        args.extend(paths)
    extract_archive(command(args, binary=True), destination, only_missing=only_missing)


def dependency_pins(cache):
    text = command(["git", "--git-dir", cache, "show", DEPENDENCY_COMMIT + ":.gitmodules"])
    config = configparser.ConfigParser()
    config.read_string(text)
    urls = {config[section]["path"]: config[section]["url"] for section in config.sections()}
    records = []
    entries = command(["git", "--git-dir", cache, "ls-tree", DEPENDENCY_COMMIT + ":thirdparty"])
    # Runtime build needs no desktop loader, MoltenVK source build, codegen
    # template engine, profiling, or test framework. All retained pins are the
    # exact gitlinks of the official 0.10.0 source, never moving branch tips.
    excluded = {"vulkan-loader", "moltenvk", "inja", "tracy", "catch2"}
    for line in entries.splitlines():
        metadata, name = line.split("\t", 1)
        mode, kind, commit = metadata.split()
        if mode == "160000" and name not in excluded:
            records.append({"name": name, "repository": urls["thirdparty/" + name], "commit": commit})
    return records


def fetch_dependencies(cache_root, sdk, offline=False):
    upstream = git_cache(cache_root, DEPENDENCY_REPOSITORY, DEPENDENCY_COMMIT, offline)
    archive_tree(upstream, DEPENDENCY_COMMIT, sdk, ["thirdparty"], only_missing=True)
    (sdk / "LICENSE.rexglue-BSD-3-Clause").write_bytes(command(
        ["git", "--git-dir", upstream, "show", DEPENDENCY_COMMIT + ":LICENSE"], binary=True))
    pins = dependency_pins(upstream)
    for pin in pins:
        git = git_cache(cache_root, pin["repository"], pin["commit"], offline)
        # Third-party modifications supplied by the Horizon fork win over the
        # original vendor checkout, matching its documented fetch policy.
        archive_tree(git, pin["commit"], sdk / "thirdparty" / pin["name"], only_missing=True)
    return pins


def inventory(root):
    return [{"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": sha(path)}
            for path in sorted(root.rglob("*")) if path.is_file() and ".git" not in path.relative_to(root).parts]


def generated_sources(root):
    allowed = {".h", ".hpp", ".cpp", ".c", ".cmake", ".json", ".stamp", ".d", ".md", ".txt"}
    for entry in root.rglob("*"):
        if entry.is_symlink() or (entry.is_file() and entry.suffix.lower() not in allowed):
            raise ValueError("generated tree contains a symlink or non-source input: " + entry.name)
    required = ["sources.cmake", "prototype_init.cpp", "prototype_init.h", "prototype_pch.h", "prototype_register.cpp"]
    if any(not (root / name).is_file() for name in required):
        raise ValueError("generated/default lacks the real Prototype translation inputs")
    names = re.findall(r"\$\{CMAKE_CURRENT_LIST_DIR\}/([^\s)]+\.cpp)", (root / "sources.cmake").read_text(encoding="utf8"))
    if not names or len(names) != len(set(names)) or not any(name.startswith("prototype_recomp.") for name in names):
        raise ValueError("generated source list has no unique real recompilation units")
    for name in names:
        source = (root / name).resolve()
        if not source.is_relative_to(root.resolve()) or not source.is_file():
            raise ValueError("generated source list escapes its tree or references a missing unit")
    pch = (root / "prototype_pch.h").read_text(encoding="utf8")
    if "REX_PHYS_HOST_OFFSET" not in pch or "REX_PLATFORM_MAC && REX_ARCH_ARM64" not in pch or "0x1000u" not in pch:
        raise ValueError("generated translation does not contain the Apple E0 adjustment")
    return names


def apple_fiber_source(text):
    """Adapt the reviewed cooperative backend's symbols/directives to Mach-O."""
    text = replace_once(text, "#if REX_PLATFORM_SWITCH\n", "#if defined(REX_PLATFORM_IOS)\n")
    for symbol in ("rex_fiber_swap", "rex_fiber_entry"):
        text = text.replace(".global " + symbol, ".global _" + symbol).replace('"' + symbol + ':', '"_' + symbol + ':')
    text = text.replace("b   rex_fiber_trampoline", "b   _rex_fiber_trampoline")
    # The final .size literal also closes the __asm__(...) C++ statement.
    # Retain its suffix while removing ELF-only assembler directives.
    return re.sub(r'(?m)^(\s*)"\.(?:type|size) [^"\n]*"(.*)$',
                  lambda match: match.group(1) + '""' + match.group(2), text)


def apple_memory_source(text):
    """Use public iPhoneOS VM APIs and a sandbox-local shared mapping backing."""
    text = replace_once(text, "#include <cstddef>",
                        "#include <cstddef>\n#include <cstdlib>\n#include <system_error>")
    text = replace_once(text, "#include <mach/mach_vm.h>",
                        "#if !defined(REX_PLATFORM_IOS) || !REX_PLATFORM_IOS\n#include <mach/mach_vm.h>\n#endif")
    old = '''  mach_vm_address_t address = reinterpret_cast<mach_vm_address_t>(base_address);
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
                               reinterpret_cast<mach_vm_address_t>(base_address));'''
    new = '''#if defined(REX_PLATFORM_IOS) && REX_PLATFORM_IOS
  using RegionAddress = vm_address_t;
  using RegionSize = vm_size_t;
#else
  using RegionAddress = mach_vm_address_t;
  using RegionSize = mach_vm_size_t;
#endif
  static_assert(sizeof(RegionAddress) == sizeof(uintptr_t), "Mach region address must preserve host pointers");
  const RegionAddress requested = reinterpret_cast<RegionAddress>(base_address);
  RegionAddress address = requested;
  RegionSize region_size = 0;
  vm_region_basic_info_data_64_t info;
  mach_msg_type_number_t info_count = VM_REGION_BASIC_INFO_COUNT_64;
  mach_port_t object_name = MACH_PORT_NULL;

#if defined(REX_PLATFORM_IOS) && REX_PLATFORM_IOS
  kern_return_t kr =
      vm_region_64(mach_task_self(), &address, &region_size, VM_REGION_BASIC_INFO_64,
                   reinterpret_cast<vm_region_info_t>(&info), &info_count, &object_name);
#else
  kern_return_t kr =
      mach_vm_region(mach_task_self(), &address, &region_size, VM_REGION_BASIC_INFO_64,
                     reinterpret_cast<vm_region_info_t>(&info), &info_count, &object_name);
#endif
  if (object_name != MACH_PORT_NULL) {
    (void)mach_port_deallocate(mach_task_self(), object_name);
  }
  if (kr != KERN_SUCCESS) {
    return false;
  }
  // A region query may advance to the next mapping when the input is a hole.
  // Subtraction after checking the lower bound avoids address+size overflow.
  if (address > requested || requested - address >= region_size) {
    return false;
  }

  length = static_cast<size_t>(region_size - (requested - address));'''
    text = replace_once(text, old, new)
    # The memory-query unit fixture exercises only this function. The full
    # pinned SDK source also contains the shared file-mapping implementation.
    if "  int oflag;\n  switch (access) {" not in text:
        return text
    text = replace_once(text,
'''  int oflag;
  switch (access) {
    case PageAccess::kNoAccess:
      oflag = 0;
      break;
    case PageAccess::kReadOnly:
    case PageAccess::kExecuteReadOnly:
      oflag = O_RDONLY;
      break;
    case PageAccess::kReadWrite:
    case PageAccess::kExecuteReadWrite:
      oflag = O_RDWR;
      break;
    default:
      assert_always();
      return kFileMappingHandleInvalid;
  }
  oflag |= O_CREAT;
  auto full_path = MakeShmName(path);
  int ret = shm_open(full_path.c_str(), oflag, 0777);
  if (ret < 0) {
    return kFileMappingHandleInvalid;
  }
  if (rex_ftruncate64(ret, static_cast<off_t>(length)) != 0) {
    close(ret);
    shm_unlink(full_path.c_str());
    return kFileMappingHandleInvalid;
  }
  return static_cast<FileMappingHandle>(ret);
#endif''',
'''  int ret = -1;
#if defined(REX_PLATFORM_IOS) && REX_PLATFORM_IOS
  // Use an unlinked sparse file in this app's own temporary directory as the
  // MAP_SHARED backing. This avoids a named shared-memory object and leaves no
  // temporary directory entry after the descriptor has been created.
  std::error_code temp_error;
  const auto temp_directory = std::filesystem::temp_directory_path(temp_error);
  if (temp_error) {
    REXSYS_ERROR("iOS guest backing: cannot resolve app temporary directory: {} ({})",
                 temp_error.message(), temp_error.value());
    return kFileMappingHandleInvalid;
  }
  std::string temporary_name = (temp_directory / "prototype-memory-XXXXXX").string();
  ret = mkstemp(temporary_name.data());
  if (ret < 0) {
    const int err = errno;
    REXSYS_ERROR("iOS guest backing: mkstemp in {} failed: {} ({})",
                 temp_directory.string(), std::strerror(err), err);
    errno = err;
    return kFileMappingHandleInvalid;
  }
  if (unlink(temporary_name.c_str()) != 0) {
    const int err = errno;
    close(ret);
    REXSYS_ERROR("iOS guest backing: unlink of temporary mapping file failed: {} ({})",
                 std::strerror(err), err);
    errno = err;
    return kFileMappingHandleInvalid;
  }
  const int descriptor_flags = fcntl(ret, F_GETFD);
  if (descriptor_flags < 0 || fcntl(ret, F_SETFD, descriptor_flags | FD_CLOEXEC) != 0) {
    const int err = errno;
    close(ret);
    REXSYS_ERROR("iOS guest backing: setting close-on-exec failed: {} ({})",
                 std::strerror(err), err);
    errno = err;
    return kFileMappingHandleInvalid;
  }
#else
  int oflag;
  switch (access) {
    case PageAccess::kNoAccess:
      oflag = 0;
      break;
    case PageAccess::kReadOnly:
    case PageAccess::kExecuteReadOnly:
      oflag = O_RDONLY;
      break;
    case PageAccess::kReadWrite:
    case PageAccess::kExecuteReadWrite:
      oflag = O_RDWR;
      break;
    default:
      assert_always();
      return kFileMappingHandleInvalid;
  }
  oflag |= O_CREAT;
  auto full_path = MakeShmName(path);
  ret = shm_open(full_path.c_str(), oflag, 0777);
  if (ret < 0) {
    const int err = errno;
    REXSYS_ERROR("shm_open({}) failed: {} ({})", full_path, std::strerror(err), err);
    errno = err;
    return kFileMappingHandleInvalid;
  }
#endif
  if (rex_ftruncate64(ret, static_cast<off_t>(length)) != 0) {
    const int err = errno;
    close(ret);
#if !defined(REX_PLATFORM_IOS) || !REX_PLATFORM_IOS
    shm_unlink(full_path.c_str());
#endif
    REXSYS_ERROR("guest backing resize to 0x{:X} bytes failed: {} ({})",
                 length, std::strerror(err), err);
    errno = err;
    return kFileMappingHandleInvalid;
  }
  return static_cast<FileMappingHandle>(ret);
#endif''')
    text = replace_once(text,
'''#if !REX_PLATFORM_ANDROID
  auto full_path = MakeShmName(path);
  shm_unlink(full_path.c_str());
#endif''',
'''#if !REX_PLATFORM_ANDROID && (!defined(REX_PLATFORM_IOS) || !REX_PLATFORM_IOS)
  auto full_path = MakeShmName(path);
  shm_unlink(full_path.c_str());
#endif''')
    return text


def apple_input_source(text):
    text = replace_once(text, '      SDL_Gamepad_initialized_ = true;', '''      SDL_Gamepad_initialized_ = true;

      // UIKit/importer or a host frontend may initialize SDL before the SDK
      // subscribes. SDL does not replay already-delivered device-added events.
      // Queue a snapshot through the same buffered event path as hotplug.
      int existing_count = 0;
      SDL_JoystickID* existing = SDL_GetGamepads(&existing_count);
      if (existing) {
        for (int i = 0; i < existing_count; ++i) {
          SDL_Event added{};
          added.type = SDL_EVENT_GAMEPAD_ADDED;
          added.gdevice.which = existing[i];
          HandleEvent(added);
        }
        SDL_free(existing);
      }''')
    return replace_once(text,
        'void SDLInputDriver::OnControllerDeviceAddedLocked(const SDL_Event& event) {',
        '''void SDLInputDriver::OnControllerDeviceAddedLocked(const SDL_Event& event) {
  // The startup snapshot can overlap a real hotplug event. Keep one SDK device
  // per SDL instance and leave connection order / guest assignment unchanged.
  if (GetControllerIndexFromInstanceID(event.gdevice.which)) return;''')


def overlay_sdk(sdk, diff_root):
    """Apply exact reviewed source edits and keep every before/after hash/diff."""
    records = []

    def write(name, transform):
        path = sdk / name
        before = path.read_bytes()
        if source_sha(path) != PATCHED_SDK_INPUTS[name]:
            raise ValueError("patched SDK source differs from reviewed input: " + name)
        text = before.decode("utf8").replace("\r\n", "\n")
        changed = transform(text)
        if changed == text:
            raise ValueError("source overlay did not change " + name)
        path.write_text(changed, encoding="utf8", newline="\n")
        target = diff_root / (name.replace("/", "__") + ".patch")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("".join(difflib.unified_diff(text.splitlines(True), changed.splitlines(True),
                                                   "a/sdk/" + name, "b/sdk/" + name)), encoding="utf8")
        records.append({"path": "sdk/" + name, "before_sha256": hashlib.sha256(before).hexdigest(),
                        "after_sha256": sha(path), "diff": target.name})

    def top(text):
        text = replace_once(text, "# Propagate profiling defines globally", '''option(REXGLUE_BUILD_TOOLS "Build host code generation tools" ON)
option(REXGLUE_STATIC_RUNTIME "Link runtime and GPU implementation into one executable" OFF)
if(CMAKE_SYSTEM_NAME STREQUAL "iOS")
    set(REXGLUE_PLATFORM_IOS ON)
    add_compile_definitions(REX_PLATFORM_IOS=1)
endif()
# Propagate profiling defines globally''')
        text = replace_once(text, "if(NOT REXGLUE_PLATFORM_SWITCH)\n    # Host tools.",
                            "if(NOT REXGLUE_PLATFORM_SWITCH AND REXGLUE_BUILD_TOOLS)\n    # Host tools.")
        text = replace_once(text, 'elseif(APPLE)\n    if(REX_TARGET_PROCESSOR MATCHES "AMD64|x86_64")',
                            'elseif(REXGLUE_PLATFORM_IOS)\n    set(REX_PLATFORM "ios-arm64")\n    add_compile_definitions(REX_PLATFORM_MAC=1)\nelseif(APPLE)\n    if(REX_TARGET_PROCESSOR MATCHES "AMD64|x86_64")')
        return text

    write("CMakeLists.txt", top)
    write("src/system/CMakeLists.txt", lambda text: replace_once(text,
          "if(REXGLUE_PLATFORM_SWITCH)\n    # A NRO", "if(REXGLUE_PLATFORM_SWITCH OR REXGLUE_STATIC_RUNTIME)\n    # A NRO"))
    write("src/graphics/CMakeLists.txt", lambda text: replace_once(text,
          "if(REXGLUE_PLATFORM_SWITCH)\n    # Linked", "if(REXGLUE_PLATFORM_SWITCH OR REXGLUE_STATIC_RUNTIME)\n    # Linked"))
    write("thirdparty/CMakeLists.txt", lambda text: replace_once(replace_once(replace_once(text,
          "if(APPLE)\n        list(APPEND REQUIRED_SUBMODULES vulkan-loader moltenvk)",
          "if(APPLE AND NOT REXGLUE_STATIC_RUNTIME)\n        list(APPEND REQUIRED_SUBMODULES vulkan-loader moltenvk)"),
          "if(APPLE)\n        # Build the loader", "if(APPLE AND NOT REXGLUE_STATIC_RUNTIME)\n        # Build the loader"),
          "if(REXGLUE_PLATFORM_SWITCH)\n    # Its default", "if(REXGLUE_PLATFORM_SWITCH OR REXGLUE_PLATFORM_IOS)\n    # Its default"))
    write("src/system/gpu_plugin_loader.cpp", lambda text: replace_once(text,
          "#if REX_PLATFORM_SWITCH\n}  // namespace rex::system",
          "#if REX_PLATFORM_SWITCH || defined(REXGLUE_STATIC_RUNTIME)\n}  // namespace rex::system"))

    def vulkan(text):
        text = replace_once(text, "#if REX_PLATFORM_SWITCH\n// NVK", "#if REX_PLATFORM_SWITCH || defined(REXGLUE_STATIC_RUNTIME)\n// NVK")
        text = replace_once(text, "#if REX_PLATFORM_MAC\n  const MacOSVulkanRuntimePaths", '''#if defined(REXGLUE_STATIC_RUNTIME)
  ifn.vkGetInstanceProcAddr = &::vkGetInstanceProcAddr;
  ifn.vkDestroyInstance = &::vkDestroyInstance;
  loader_loaded = true;
#elif REX_PLATFORM_MAC
  const MacOSVulkanRuntimePaths''')
        return replace_once(text, "#if !REX_PLATFORM_SWITCH\n#define XE_VULKAN_LOAD_LOADER_FUNCTION",
                            "#if !REX_PLATFORM_SWITCH && !defined(REXGLUE_STATIC_RUNTIME)\n#define XE_VULKAN_LOAD_LOADER_FUNCTION")

    write("src/ui/vulkan/vulkan_instance.cpp", vulkan)
    def vulkan_device_lifecycle(text):
        text = replace_once(
            text,
            "namespace rex {\nnamespace ui {\nnamespace vulkan {",
            "namespace rex {\nnamespace ui {\nnamespace vulkan {\n\nvoid prototype_vulkan_install_lifecycle_gate(VulkanDevice::Functions*, VkDevice, PFN_vkGetDeviceProcAddr);")
        return replace_once(
            text,
            "  // Get the queues.\n",
            "  prototype_vulkan_install_lifecycle_gate(&dfn, device->device_,\n"
            "      device->vulkan_instance()->functions().vkGetDeviceProcAddr);\n\n  // Get the queues.\n")
    write("src/ui/vulkan/vulkan_device.cpp", vulkan_device_lifecycle)
    # Runtime SPIRV validation is optional in this SDK already. Preserve the
    # explicit false result for a static build rather than searching host SDKs
    # or pretending validation ran. Shader compilation itself remains original.
    def spirv(text):
        text = replace_once(text, "bool SpirvToolsContext::Initialize(unsigned int spirv_version) {", '''bool SpirvToolsContext::Initialize(unsigned int spirv_version) {
#if defined(REXGLUE_STATIC_RUNTIME)
  (void)spirv_version;
  REXLOG_INFO("SPIRV-Tools runtime validation is unavailable in this static build");
  return false;
#else''')
        return replace_once(text, "  return true;\n}\n\nvoid SpirvToolsContext::Shutdown()",
                            "  return true;\n#endif\n}\n\nvoid SpirvToolsContext::Shutdown()")
    write("src/ui/vulkan/spirv_tools_context.cpp", spirv)
    write("src/core/memory_posix.cpp", apple_memory_source)
    write("src/input/sdl/sdl_input_driver.cpp", apple_input_source)
    # Preserve underrun counts at the shipped warn log level. The endpoint
    # format and missing-frame ratio distinguish starvation from bad decoding.
    from apple_native_fixes import audio_source
    write("src/audio/sdl/sdl_audio_driver.cpp", audio_source)
    from apple_native_fixes import translator_source
    write("src/graphics/pipeline/shader/spirv_translator.cpp", translator_source)
    from apple_native_fixes import exception_source
    write("src/core/exception_handler_posix.cpp", exception_source)

    write("include/rex/thread/fiber.h", lambda text: text.replace(
          "#if REX_PLATFORM_SWITCH\n", "#if REX_PLATFORM_SWITCH || defined(REX_PLATFORM_IOS)\n").replace(
          "#elif REX_PLATFORM_SWITCH\n", "#elif REX_PLATFORM_SWITCH || defined(REX_PLATFORM_IOS)\n"))
    write("src/core/CMakeLists.txt", lambda text: replace_once(text, "endif()\n\n# Xenos data-format layer", '''endif()
if(REXGLUE_PLATFORM_IOS)
    get_target_property(_prototype_core_sources rexcore SOURCES)
    list(REMOVE_ITEM _prototype_core_sources fiber_posix.cpp)
    set_property(TARGET rexcore PROPERTY SOURCES "${_prototype_core_sources}")
    target_sources(rexcore PRIVATE fiber_apple.cpp)
endif()

# Xenos data-format layer'''))

    fiber_source = sdk / "src/core/fiber_switch.cpp"
    if source_sha(fiber_source) != PATCHED_SDK_INPUTS["src/core/fiber_switch.cpp"]:
        raise ValueError("fiber adaptation source differs")
    fiber = apple_fiber_source(fiber_source.read_text(encoding="utf8"))
    target = sdk / "src/core/fiber_apple.cpp"
    target.write_text("// Apple Mach-O adaptation of the adjacent BSD-licensed cooperative fiber backend.\n" + fiber,
                      encoding="utf8", newline="\n")
    records.append({"path": "sdk/src/core/fiber_apple.cpp", "derived_from": "sdk/src/core/fiber_switch.cpp",
                    "source_sha256": sha(fiber_source), "after_sha256": sha(target),
                    "verified_runtime": False})
    write("src/ui/windowed_app_main_sdl.cpp", lambda text: replace_once(text,
          "#include <rex/cvar.h>", '#if defined(REX_PLATFORM_IOS) && !defined(SDL_MAIN_HANDLED)\n#include <SDL3/SDL_main.h>\n#endif\n\n#include <rex/cvar.h>'))
    return records


def prepare(args):
    out, upstream = args.out.resolve(), args.prototype_root.resolve()
    generated = args.generated.resolve()
    if out.exists() or out.is_relative_to(upstream) or upstream.is_relative_to(out) or out.is_relative_to(generated):
        raise ValueError("preparation requires a fresh output separate from every input")
    if command(["git", "-C", upstream, "rev-parse", "HEAD"]) != PROTOTYPE_COMMIT:
        raise ValueError("Prototype source is not the reviewed pinned revision")
    if command(["git", "-C", upstream, "status", "--porcelain", "--", "prototype", "patches", "LICENSE"]):
        raise ValueError("reviewed Prototype source has local modifications")
    names = generated_sources(generated)
    patch = upstream / "patches/rexglue-sdk-masseffect-nx.patch"
    patch_bytes = command(["git", "-C", upstream, "show", PATCH_COMMIT + ":patches/rexglue-sdk-masseffect-nx.patch"], binary=True)
    codegen_patch = upstream / "prototype/tools/prepare_proto_codegen.py"
    if hashlib.sha256(patch_bytes.replace(b"\r\n", b"\n")).hexdigest() != PATCH_SHA256 or source_sha(codegen_patch) != CODEGEN_PATCH_SHA256:
        raise ValueError("Prototype compatibility patch or generated-code transform differs")
    out.mkdir(parents=True)
    logs, diffs = out / "logs", out / "overlays"
    logs.mkdir(); diffs.mkdir()
    sdk_cache = git_cache(args.cache.resolve(), SDK_REPOSITORY, SDK_COMMIT, args.offline)
    archive_tree(sdk_cache, SDK_COMMIT, out, ["sdk"])
    sdk = out / "sdk"
    for original, target in (("LICENSE", "LICENSE.masseffect-nx-GPL-3.0"),
                             ("THIRD_PARTY_NOTICES.md", "NOTICE.masseffect-nx.md")):
        (sdk / target).write_bytes(command(["git", "--git-dir", sdk_cache, "show", SDK_COMMIT + ":" + original], binary=True))
    for heading in re.findall(r"^diff --git (\S+) (\S+)$", patch_bytes.decode("utf8"), re.MULTILINE):
        if any(not path.startswith(prefix) or ".." in PurePosixPath(path).parts
               for path, prefix in zip(heading, ("a/sdk/", "b/sdk/"))):
            raise ValueError("compatibility patch leaves the SDK tree")
    # Bind Git to the isolated SDK root. Without an explicit work tree, Git
    # discovers the parent port repository and silently skips SDK-relative
    # patch paths when invoked from a nested preparation directory.
    canonical_patch = diffs / "upstream-prototype-sdk.patch"
    canonical_patch.write_bytes(patch_bytes.replace(b"\r\n", b"\n"))
    apply = ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "--git-dir", sdk_cache, "--work-tree", sdk, "apply"]
    command([*apply, "--check", "--whitespace=nowarn", "-p2", canonical_patch], cwd=sdk)
    command([*apply, "--whitespace=nowarn", "-p2", canonical_patch], cwd=sdk)
    # Hash-check overlays before dependency population, which keeps fork edits.
    overlays = overlay_sdk(sdk, diffs)
    dependencies = fetch_dependencies(args.cache.resolve(), sdk, args.offline) if args.fetch_dependencies else []
    prototype = out / "prototype"
    prototype.mkdir()
    shutil.copytree(upstream / "prototype/src", prototype / "src")
    # Keep the iOS-proven stable renderer core while importing upstream's
    # generated-code speed improvements. The current header includes upstream's
    # equivalent register-kind lookup, avoiding a branch chain per register write.
    for relative in ("native/native_renderer.cpp", "native/p2_native_system.cpp"):
        data = command(["git", "-C", upstream, "show", PATCH_COMMIT + ":prototype/src/" + relative], binary=True)
        (prototype / "src" / relative).write_bytes(data)
    native_system = prototype / "src/native/p2_native_system.cpp"
    system_text = native_system.read_text(encoding="utf8")
    system_text = replace_once(system_text, "  void WriteRegister(uint32_t index, uint32_t value) {", '''  void WriteRegister(uint32_t index, uint32_t value) {
    // Upstream fast path: special MMIO handlers all live below 0x1928.
    if (index >= 0x1928 && index < kRegisterCount) [[likely]] {
      const uint32_t old_value = registers_[index];
      if (old_value == value) return;
      registers_[index] = value;
      renderer_.RegisterWritten(index, old_value, value);
      return;
    }
    WriteRegisterSlow(index, value);
  }

  void WriteRegisterSlow(uint32_t index, uint32_t value) {''')
    system_text = replace_once(system_text, "    const RendererStats& s = renderer_.stats();", '''    const RendererStats& s = renderer_.stats();
    // Reuse the existing per-frame counters; no clocks on register/draw paths.
    // ring_idle is time awaiting commands, not necessarily idle guest CPU time.
    struct ApplePacingWindow {
      uint32_t count = 0;
      double wall_ms = 0;
      uint64_t idle_ns = 0, poll_ns = 0, hold_ns = 0;
    };
    static ApplePacingWindow apple_pacing;
    if (s.frame_draws >= 1200) {
      ++apple_pacing.count;
      apple_pacing.wall_ms += ms;
      apple_pacing.idle_ns += idle_ns;
      apple_pacing.poll_ns += game_wait_ns;
      apple_pacing.hold_ns += hold_ns;
      if (apple_pacing.count == 300) {
        REXLOG_WARN("[apple-pacing] scene_frames=300 frame_ms={:.2f} ring_idle_ms={:.2f} guest_poll_ms={:.2f} host_hold_ms={:.2f}",
            apple_pacing.wall_ms / 300.0, double(apple_pacing.idle_ns) / 300000000.0,
            double(apple_pacing.poll_ns) / 300000000.0, double(apple_pacing.hold_ns) / 300000000.0);
        apple_pacing = {};
      }
    } else {
      apple_pacing = {};
    }''')
    native_system.write_text(system_text, encoding="utf8", newline="\n")
    render_thread = prototype / "src/proto_render_thread.cpp"
    if render_thread.is_file():
        text = render_thread.read_text(encoding="utf8")
        old = '''extern "C" void RexSwitchSetCurrentThreadPriority(int priority);
extern "C" bool RexSwitchSetCurrentThreadMask(uint32_t mask);'''
        new = '''#if defined(REX_PLATFORM_SWITCH) && !defined(REX_PLATFORM_IOS)
extern "C" void RexSwitchSetCurrentThreadPriority(int priority);
extern "C" bool RexSwitchSetCurrentThreadMask(uint32_t mask);
#else
static void RexSwitchSetCurrentThreadPriority(int) {}
static bool RexSwitchSetCurrentThreadMask(uint32_t) { return false; }
#endif'''
        if text.count(old) != 1:
            raise ValueError("Prototype render-thread API anchor changed")
        render_thread.write_text(text.replace(old, new, 1), encoding="utf8", newline="\n")
    app_header = prototype / "src/prototype_app.h"
    app_before = app_header.read_text(encoding="utf8")
    app_after = replace_once(app_before, "#include <filesystem>", "#include <cstdlib>\n#include <filesystem>")
    app_after = replace_once(app_after,
        "    if (!paths.game_data_root.empty()) return;",
        "    if (const char* config = std::getenv(\"PROTOTYPE_CONFIG_PATH\")) paths.config_path = config;\n"
        "    if (!paths.game_data_root.empty()) return;")
    app_header.write_text(app_after, encoding="utf8", newline="\n")
    app_diff = diffs / "prototype-documents-config.patch"
    app_diff.write_text("".join(difflib.unified_diff(app_before.splitlines(True), app_after.splitlines(True),
        "a/prototype/src/prototype_app.h", "b/prototype/src/prototype_app.h")), encoding="utf8")
    overlays.append({"path": "prototype/src/prototype_app.h", "before_sha256": hashlib.sha256(app_before.encode()).hexdigest(),
                     "after_sha256": sha(app_header), "diff": app_diff.name})
    for name in ("prototype.toml", "prototype_manifest.toml", "overrides.toml"):
        shutil.copyfile(upstream / "prototype" / name, prototype / name)
    shutil.copyfile(upstream / "LICENSE", out / "Prototype-LICENSE")
    input_inventory = inventory(generated)
    gen = prototype / "generated/default"
    shutil.copytree(generated, gen)
    # This reviewed transform adds the existing game's address-striped atomic
    # regions and render-size hooks. It operates only on the copied generated
    # source and does not invoke the upstream build or the game.
    with (logs / "prepare-generated.log").open("w", encoding="utf8") as output:
        subprocess.run([sys.executable, str(codegen_patch), str(gen)], check=True, stdout=output, stderr=subprocess.STDOUT)
    speed_root = upstream / "tools/mgr"
    speed_steps = [("guest_cas.py", ["--gen", str(gen)]),
                   ("vec_byteswap.py", ["--gen", str(gen)]),
                   ("pch_neon_shuffles.py", [str(gen / "prototype_pch.h")])]
    if not all((speed_root / name).is_file() for name, _ in speed_steps):
        raise ValueError("Prototype 1.4.0 speed transform is missing")
    speed_records = []
    for name, arguments in speed_steps:
        before_speed = inventory(gen)
        command([sys.executable, str(speed_root / name), *arguments])
        speed_records.append({"tool": name, "before": before_speed, "after": inventory(gen)})
    if (speed_root / "LICENSE-mgr-nx-deko").is_file():
        shutil.copyfile(speed_root / "LICENSE-mgr-nx-deko", out / "mgr-nx-deko-LICENSE")
    renderer = prototype / "src/native/native_renderer.cpp"
    renderer_before = renderer.read_bytes()
    before = renderer_before.decode("utf8").replace("\r\n", "\n")
    after = replace_once(before, 'extern "C" void RexSwitchPerfSceneFrame(uint32_t draws);',
                         '#if defined(__SWITCH__)\nextern "C" void RexSwitchPerfSceneFrame(uint32_t draws);\n#endif')
    after = replace_once(after, 'extern "C" void RexSwitchScreenshotRequest() __attribute__((weak));',
                         '#if !defined(__APPLE__)\nextern "C" void RexSwitchScreenshotRequest() __attribute__((weak));\n#endif')
    # Mach-O weak_import still needs a linked provider. The original callback
    # is optional and has no Apple implementation; preserve water-debug mode
    # cycling while reporting that its Switch screenshot service is absent.
    after = replace_once(after,
                         '    if (RexSwitchScreenshotRequest) RexSwitchScreenshotRequest();\n'
                         '    REXLOG_INFO("[native] Water debug: screenshot of mode {}", water_debug_mode);',
                         '#if defined(__APPLE__)\n'
                         '    REXLOG_WARN("[native] Water debug: screenshot callback unavailable (mode {})", water_debug_mode);\n'
                         '#else\n'
                         '    if (RexSwitchScreenshotRequest) RexSwitchScreenshotRequest();\n'
                         '    REXLOG_INFO("[native] Water debug: screenshot of mode {}", water_debug_mode);\n'
                         '#endif')
    after = replace_once(after, "  RexSwitchPerfSceneFrame(uint32_t(frame_draw_count));",
                         "#if defined(__SWITCH__)\n  RexSwitchPerfSceneFrame(uint32_t(frame_draw_count));\n#endif")
    # Narrow bitfields are promoted to signed int. Convert before shifting so
    # the GPU page address remains a uint32_t in both rendering and tracing.
    after = replace_once(after, "    address = fetch.base_address << 12;",
                         "    address = uint32_t(fetch.base_address) << 12;")
    after = replace_once(after, "frontbuffer & 0x1FFFFFFF,fetch.base_address<<12,address,width,height,",
                         "frontbuffer & 0x1FFFFFFF,uint32_t(fetch.base_address)<<12,address,width,height,")
    # The pinned SDK already implements point/rectangle VS expansion. Connect
    # its actual index-loading contract instead of inserting tessellation.
    from apple_native_fixes import renderer_source
    after = renderer_source(after)
    shutil.copyfile(ROOT / "ios/source-port/PrototypePrimitiveIndices.h",
                    prototype / "src/native/PrototypePrimitiveIndices.h")
    renderer.write_text(after, encoding="utf8", newline="\n")
    renderer_diff = diffs / "prototype-native-renderer.patch"
    renderer_diff.write_text("".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                           "a/prototype/src/native/native_renderer.cpp", "b/prototype/src/native/native_renderer.cpp")), encoding="utf8")
    overlays.append({"path": "prototype/src/native/native_renderer.cpp", "before_sha256": hashlib.sha256(renderer_before).hexdigest(),
                     "after_sha256": sha(renderer), "diff": renderer_diff.name})
    shutil.copyfile(ROOT / "ios/source-port/CMakeLists.txt", out / "CMakeLists.txt")
    if (ROOT / "ios/source-port/Info.plist.in").is_file():
        shutil.copyfile(ROOT / "ios/source-port/Info.plist.in", out / "Info.plist.in")
    report = {"schema": 1, "kind": "original-source-apple-build-inputs", "compiled": False,
              "runtime_verified": False, "playable_verified": False, "contains_game_resources": False,
              "prototype_commit": PROTOTYPE_COMMIT, "sdk_repository": SDK_REPOSITORY, "sdk_commit": SDK_COMMIT,
              "renderer_core_commit": PATCH_COMMIT,
              "renderer_register_lookup_commit": PROTOTYPE_COMMIT,
              "native_system_commit": PATCH_COMMIT,
              "native_system_overlays": ["upstream-register-write-fast-path", "apple-lifecycle-and-pacing"],
              "compatibility_patch_sha256": PATCH_SHA256, "dependency_source_commit": DEPENDENCY_COMMIT,
              "dependency_pins": dependencies, "dependencies_populated": args.fetch_dependencies,
              "generated_compile_units": len(names), "generated_input_files": input_inventory,
              "generated_prepared_files": inventory(gen), "speed_patches": speed_records,
              "app_source_files": inventory(prototype / "src"),
              "source_overlays": overlays, "cmake_sha256": sha(out / "CMakeLists.txt"),
              "ios_remaining": ["target-SDK compile/link and fiber tests", "UIKit resource importer and virtual controller",
                                "device memory/fault/renderer/audio/save verification"],
              "configure_example": ["cmake", "-S", str(out), "-B", str(out / "build-macos"), "-G", "Ninja",
                                    "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_OSX_ARCHITECTURES=arm64", "-DMOLTENVK_ROOT=<pinned-release-root>"],
              "limitations": ["No compilation or execution was performed by preparation.",
                              "SPIRV-Tools dynamic SDK validation remains explicitly unavailable in a static runtime build.",
                              "The Apple fiber backend is a source adaptation and has not been validated on device."]}
    (out / "source-port-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    print(json.dumps({key: report[key] for key in ("kind", "sdk_commit", "generated_compile_units", "dependencies_populated", "compiled")}))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", type=Path, required=True, help="actual generated/default from reviewed codegen")
    parser.add_argument("--prototype-root", type=Path, default=ROOT / "references/prototype-switch")
    parser.add_argument("--out", type=Path, default=ROOT / "work/source-port/apple-native")
    parser.add_argument("--cache", type=Path, default=ROOT / ".tools/source-port/git-cache")
    parser.add_argument("--fetch-dependencies", action="store_true", help="populate fixed official runtime gitlink sources")
    parser.add_argument("--offline", action="store_true", help="use already cached Git objects only")
    return prepare(parser.parse_args(argv))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        if error.stderr:
            sys.stderr.write(error.stderr.decode("utf8", errors="replace") if isinstance(error.stderr, bytes) else error.stderr)
        raise SystemExit(error.returncode) from None
