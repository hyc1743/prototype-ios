#!/usr/bin/env python3
"""Observe the real source-native game on ARM64 macOS, never claim iOS execution."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

from build_ios import moltenvk, run, sha256
from build_source_ios import expected_functions, verify_generated
from pack_resources import read_manifest, verify

ROOT = Path(__file__).resolve().parents[1]
RESOURCE_RELEASE = "https://github.com/hyc1743/prototype-ios/releases/download/resources-v1/"


def download_parts(destination, manifest):
    destination.mkdir(parents=True, exist_ok=True)
    for part in manifest["parts"]:
        target = destination / part["name"]
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError("Resource destination must be a regular file")
        if target.exists() and target.stat().st_size == part["size"] and sha256(target) == part["sha256"]:
            continue
        temporary = target.with_name(target.name + ".partial")
        try:
            request = urllib.request.Request(RESOURCE_RELEASE + part["name"],
                                             headers={"User-Agent": "Prototype-source-probe"})
            count = 0
            with urllib.request.urlopen(request, timeout=180) as source, temporary.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    count += len(chunk)
                    if count > part["size"]:
                        raise ValueError("Resource download exceeds pinned size")
                    output.write(chunk)
            if count != part["size"] or sha256(temporary) != part["sha256"]:
                raise ValueError("Resource download size/SHA256 mismatch: " + part["name"])
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        print("Verified " + part["name"], flush=True)


def observations(text):
    return {
        "source_main_entered": "[prototype-mac-probe] entering original source main" in text,
        "scripted_input_events": len(re.findall(r"\[prototype-mac-probe\] input button=\d+ ms=\d+", text)),
        "frontbuffer_log_observed": "[native] Frontbuffer " in text,
        "scene_frame_diagnostic_observed": "[p2-frame] copy_phase=" in text,
        "sdk_controller_open_observed": "SDL OnControllerDeviceAdded: connection order" in text,
        "audio_data_frames_reported": sum(int(n) for n in re.findall(r"\[audio\] SDL in 10 s: (\d+) frames with data", text)),
        "pipeline_failure_lines": [line for line in text.splitlines() if "Pipeline creation failed" in line][:20],
        "device_failure_lines": [line for line in text.splitlines()
            if any(marker in line for marker in ("Lost VkDevice", "VK_ERROR_DEVICE_LOST",
                "vkQueueSubmit failed", "unhandled host fault"))][:20],
        "audio_nonzero_output_observed": any(float(value) > 0.00001 for value in
            re.findall(r"\[audio\] output peak=([0-9.eE+-]+)", text)),
    }


def runtime_status(report, controlled_termination, exit_code):
    if (report.get("device_failure_lines") or report.get("pipeline_failure_lines") or
            report.get("spirv_validation", {}).get("invalid", 0)):
        return "runtime_failed"
    return "observation_finished" if controlled_termination or exit_code == 0 else "process_failed"


def stop(process):
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=10)
        except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
            pass
    except (ProcessLookupError, PermissionError):
        pass


def probe(args):
    if sys.platform != "darwin" or platform.machine().lower() != "arm64":
        raise ValueError("Source observation requires native ARM64 macOS")
    out = args.out.resolve()
    if out.exists():
        raise ValueError("Probe output must be fresh")
    evidence = out / "evidence"
    evidence.mkdir(parents=True)
    report = {"schema": 1, "platform": "macOS", "architecture": platform.machine(),
              "status": "failed", "ios_device_execution": False,
              "runtime_verified": False, "playable_verified": False,
              "scope": "Source-native macOS diagnostic with verified resources and ordinary SDL input"}
    report["renderer_requested"] = args.renderer
    report["safe_native"] = args.safe_native
    try:
        generated = args.generated.resolve()
        verify_generated(generated)
        expected = expected_functions(generated)
        env = {k: v for k, v in os.environ.items()
               if not any(word in k.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "_KEY"))}
        mvk = moltenvk(ROOT / ".tools/moltenvk-1.4.2")
        stage, build = out / "staged", out / "build"
        run([sys.executable, ROOT / "tools/prepare_source_port.py", "--generated", generated,
             "--prototype-root", args.prototype_root.resolve(), "--out", stage, "--fetch-dependencies"],
            env=env, log=evidence / "prepare.log")
        command = ["cmake", "-S", stage, "-B", build, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
                   "-DCMAKE_OSX_ARCHITECTURES=arm64", "-DCMAKE_OSX_DEPLOYMENT_TARGET=15.0",
                   "-DMOLTENVK_ROOT=" + str(mvk),
                   "-DPROTOTYPE_APPLE_ENTRY_SOURCE=" + str(ROOT / "ios/source-port/PrototypeMacProbe.cpp")]
        if shutil.which("ccache"):
            command += ["-DCMAKE_C_COMPILER_LAUNCHER=ccache", "-DCMAKE_CXX_COMPILER_LAUNCHER=ccache"]
        run(command, env=env, log=evidence / "configure.log")
        run(["cmake", "--build", build, "--target", "prototype", "--parallel", "3"],
            env=env, log=evidence / "compile.log")
        executable = build / "PrototypeNative"
        symbols = run(["xcrun", "nm", "-U", "-j", executable], env=env)
        if expected - set(re.findall(r"sub_[0-9a-fA-F]{8}", symbols)):
            raise ValueError("macOS diagnostic is missing translated game functions")
        report.update(compiled=True, translated_functions_verified=len(expected), executable_sha256=sha256(executable))
        manifest = read_manifest(ROOT / "ios/resources/PrototypeData.manifest.json")
        parts = args.parts.resolve()
        if args.download_parts:
            download_parts(parts, manifest)
        # Manifest paths already start with game/, matching the importer layout.
        private = out / "private"
        game = private / "game"
        report["resources"] = verify(parts, manifest, restore=private)
        saves, cache = out / "private/saves", out / "private/cache"
        saves.mkdir(); cache.mkdir()
        config = out / "private/prototype.toml"
        shutil.copyfile(ROOT / "ios/config/prototype.toml", config)
        env["PROTOTYPE_CONFIG_PATH"] = str(config)
        shader_dump = out / "private/spirv"
        shader_dump.mkdir()
        env["PROTOTYPE_SPIRV_DUMP"] = str(shader_dump)
        native_dump = evidence / "render-targets"
        native_dump.mkdir()
        env["PROTOTYPE_NATIVE_DUMP"] = str(native_dump)
        arguments = [str(executable), "--game_data_root=" + str(game), "--user_data_root=" + str(saves),
                     "--cache_root=" + str(cache), "--log_file=" + str(evidence / "engine.log"),
                     "--log_level=info", "--log_flush_level=info", "--p2_frame_diagnostics=true",
                     "--p2_native_occlusion_queries=false", "--audio_dump_output_s=15",
                     "--audio_dump_output_from_s=10", "--p2_renderer=" + args.renderer]
        arguments.append("--proto_apple_debug_raster=" + str(args.debug_raster))
        if args.safe_native:
            arguments.extend(["--gpu_specialize_texture_signs=false", "--p2_native_copy_specialization=false",
                "--p2_native_structured_jumps=false", "--p2_native_binding_revalidate=false",
                "--p2_native_dynamic_buffer_upload=false", "--p2_native_sampled_stable_ranges=false",
                "--p2_native_depth_words_direct=false", "--p2_native_resolve_xenia_pack=false",
                "--p2_native_edram_depth_alias_clear=false"])
        shots, process, deadline = [], None, False
        started = time.monotonic()
        with (evidence / "run.log").open("w", encoding="utf8") as output:
            try:
                process = subprocess.Popen(arguments, env=env, cwd=out / "private", stdout=output,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                next_shot = 20
                next_sample = 60
                while process.poll() is None:
                    elapsed = time.monotonic() - started
                    if elapsed >= args.seconds:
                        deadline = True
                        break
                    if elapsed >= next_shot:
                        shot = evidence / f"screen-{next_shot:03d}.png"
                        try:
                            captured = subprocess.run(["/usr/sbin/screencapture", "-x", str(shot)], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                            if captured.returncode == 0 and shot.is_file():
                                shots.append(shot.name)
                        except subprocess.TimeoutExpired:
                            report.setdefault("screenshot_timeouts", []).append(next_shot)
                        next_shot += 20
                    if elapsed >= next_sample:
                        try:
                            subprocess.run(["/usr/bin/sample", str(process.pid), "2", "-file",
                                        str(evidence / f"threads-{next_sample:03d}.txt")],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                        except subprocess.TimeoutExpired:
                            report.setdefault("sample_timeouts", []).append(next_sample)
                        next_sample += 120
                    time.sleep(0.25)
            finally:
                if process is not None:
                    stop(process)
        text = "\n".join(p.read_text(encoding="utf8", errors="replace") for p in
                          (evidence / "run.log", evidence / "engine.log") if p.is_file())
        report.update(**observations(text), exit_code=process.returncode, controlled_termination=deadline,
                      observed_seconds=round(time.monotonic() - started, 2), screenshots=shots,
                      created_save_files=[p.relative_to(saves).as_posix() for p in saves.rglob("*") if p.is_file()])
        validator = shutil.which("spirv-val")
        shader_results = []
        if validator:
            for binary in sorted(shader_dump.glob("*.spirv.bin.*")):
                validated = subprocess.run([validator, "--target-env", "vulkan1.3", "--scalar-block-layout", str(binary)],
                                           capture_output=True, text=True, timeout=15)
                shader_results.append({"shader": binary.name, "valid": validated.returncode == 0,
                                       "diagnostic": validated.stdout + validated.stderr})
        (evidence / "spirv-validation.json").write_text(json.dumps(shader_results, indent=2) + "\n", encoding="utf8")
        report["spirv_validation"] = {"available": bool(validator), "shaders": len(shader_results),
                                     "invalid": sum(not r["valid"] for r in shader_results)}
        audio_dump = build / "audio_output.wav"
        if audio_dump.is_file():
            shutil.copyfile(audio_dump, evidence / "audio_output.wav")
        report["status"] = runtime_status(report, deadline, process.returncode)
        return report
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
        raise
    finally:
        (evidence / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", type=Path, required=True)
    parser.add_argument("--prototype-root", type=Path, default=ROOT / "references/prototype-switch")
    parser.add_argument("--out", type=Path, default=ROOT / "out/mac-source-probe")
    parser.add_argument("--parts", type=Path, default=ROOT / "out/probe-resource-parts")
    parser.add_argument("--download-parts", action="store_true")
    parser.add_argument("--seconds", type=int, choices=(60, 120, 180, 300), default=300)
    parser.add_argument("--renderer", choices=("native", "xenos"), default="native")
    parser.add_argument("--safe-native", action="store_true")
    parser.add_argument("--debug-raster", type=int, choices=(0, 1, 2, 3), default=0)
    result = probe(parser.parse_args())
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "observation_finished" else 1


if __name__ == "__main__":
    raise SystemExit(main())
