#!/usr/bin/env python3
# Chat-Linux-Gguf installer

import os
import json
import platform
import subprocess
import sys
import re
import shutil
import time
import socket
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple, List

# Constants
APP_NAME = "Chat-Linux-Gguf"
BASE_DIR = Path(__file__).parent
VENV_DIR = BASE_DIR / ".venv"
LLAMA_DIR = BASE_DIR / "llama.cpp"
BUILD_DIR = LLAMA_DIR / "build"
BIN_DIR = BASE_DIR / "data" / "llama-cpp"
CONFIG_PATH = BASE_DIR / "data" / "persistent.json"

# Minimum requirements
MIN_CUDA_VERSION = 11.0
MIN_DRIVER_VERSION = 450.80
MIN_VRAM = 2048  # 2GB VRAM
MIN_COMPUTE_CAPABILITY = 6.0

# GCC compatibility mapping for CUDA versions
CUDA_GCC_COMPATIBILITY = {
    "11.0": 9,
    "11.1": 10,
    "11.2": 10,
    "11.3": 10,
    "11.4": 10,
    "11.5": 10,
    "11.6": 10,
    "11.7": 10,
    "11.8": 11,
    "12.0": 11,
    "12.1": 12,
    "12.2": 12,
    "12.3": 12,
    "12.4": 12,
    "12.5": 12,
}

REQUIREMENTS = [
    "gradio>=4.25.0",
    "requests==2.31.0",
    "pyperclip",
    "yake",
    "psutil",
    "duckduckgo-search",
    "newspaper3k",
    "langchain-community==0.3.18",
    "pygments==2.17.2",
    "lxml_html_clean",
    "llama-cpp-python==0.2.23",
]

CONFIG_TEMPLATE = """{
    "model_settings": {
        "model_dir": "models",
        "model_name": "",
        "context_size": 8192,
        "temperature": 0.66,
        "repeat_penalty": 1.1,
        "mmap": false,
        "mlock": false,
        "n_batch": %d,
        "max_history_slots": 12,
        "max_attach_slots": 6,
        "session_log_height": 500
    },
    "backend_config": {
        "available_gpus": %s,
        "selected_gpu": %d,
        "cuda_version": "CUDA %s",
        "compute_capability": "%s"
    }
}"""

class InstallerError(Exception):
    """Custom installer exception"""
    pass

def log_message(message: str, level: str = "INFO") -> None:
    """Log message"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {level}: {message}")
    sys.stdout.flush()


def display_progress(progress: float, downloaded: int, total: int, elapsed: float, estimated: float) -> None:
    """Display progress bar with 10 steps, percent, size, and time"""
    steps = 10
    filled = int(progress * steps)
    bar = "=" * filled + "-" * (steps - filled)
    percent = min(int(progress * 100), 100)  # Cap at 100%
    
    # Format sizes appropriately
    if total > 1024:
        size_str = f"{downloaded/1024:.1f}MB/{total/1024:.1f}MB"
    else:
        size_str = f"{downloaded:.0f}KB/{total:.0f}KB"
    
    # Format time appropriately
    elapsed_str = f"{int(elapsed)}s"
    if estimated > 0 and estimated < 9999:
        estimated_str = f"{int(estimated)}s"
        time_str = f"{elapsed_str}/{estimated_str}"
    else:
        time_str = elapsed_str
    
    # Clear line and write progress
    sys.stdout.write(f"\r\033[K[{bar}] {percent}% {size_str} {time_str}")
    sys.stdout.flush()

def run_command(cmd: list, cwd: Optional[Path] = None, timeout: int = 600, 
                env: Optional[Dict[str, str]] = None, stream_output: bool = False) -> Tuple[bool, str]:
    """Execute command with optional streaming for progress"""
    output = []
    try:
        if stream_output:
            process = subprocess.Popen(
                cmd,
                cwd=str(cwd) if cwd else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                bufsize=1,
                universal_newlines=True
            )
            while True:
                line = process.stdout.readline()
                if line == "" and process.poll() is not None:
                    break
                if line:
                    output.append(line.strip())
                    sys.stdout.write(f"\r{line.strip()}")
                    sys.stdout.flush()
            return_code = process.wait(timeout=timeout)
            if return_code != 0:
                return False, "\n".join(output)
            return True, "\n".join(output)
        else:
            result = subprocess.run(
                cmd,
                cwd=str(cwd) if cwd else None,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
                env=env
            )
            return True, result.stdout
    except subprocess.CalledProcessError as e:
        return False, e.stdout
    except subprocess.TimeoutExpired:
        return False, f"Timeout after {timeout}s"

def check_sudo_available() -> bool:
    """Check if sudo is available"""
    try:
        result = subprocess.run(["sudo", "-n", "true"], 
                              capture_output=True, timeout=5)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False

def get_cuda_compute_capabilities() -> List[str]:
    """Fetch GPU capabilities"""
    try:
        smi_output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader,nounits"],
            text=True
        ).strip()
        capabilities = list(set(line.strip() for line in smi_output.split('\n') if line.strip()))
        return capabilities
    except Exception as e:
        log_message(f"Capability detection failed: {e}", "WARNING")
        time.sleep(1)
        return []

def get_cuda_architecture_flags() -> Tuple[List[str], str]:
    """Generate CUDA flags"""
    capabilities = get_cuda_compute_capabilities()
    if not capabilities:
        log_message("Using default architectures", "WARNING")
        time.sleep(1)
        arch_string = "60;61;70;75;80;86;89;90"
        return [f"-DCMAKE_CUDA_ARCHITECTURES={arch_string}"], arch_string
    architectures = sorted(set(cap.replace('.', '') for cap in capabilities if len(cap.replace('.', '')) == 2))
    arch_string = ";".join(architectures) if architectures else "60;61;70;75;80;86;89;90"
    log_message(f"Detected architectures: {arch_string}")
    time.sleep(1)
    return [f"-DCMAKE_CUDA_ARCHITECTURES={arch_string}"], arch_string

def check_unified_memory_support(compute_cap: str) -> bool:
    """Verify memory support"""
    try:
        return float(compute_cap) >= MIN_COMPUTE_CAPABILITY
    except (ValueError, TypeError):
        return False

def check_cuda_compatibility(cuda_version: str, gpus: List[Dict[str, any]]) -> bool:
    """Check CUDA compatibility"""
    cuda_major = float(cuda_version.split('.')[0])
    for gpu in gpus:
        compute_cap = float(gpu["compute_capability"])
        if cuda_major >= 12 and compute_cap < 6.0:
            log_message(f"CUDA {cuda_version} incompatible", "WARNING")
            time.sleep(1)
            return False
        if cuda_major < 12 and compute_cap >= 8.0:
            log_message(f"CUDA {cuda_version} suboptimal", "WARNING")
            time.sleep(1)
    return True

def select_gpu(gpus: List[Dict[str, any]]) -> int:
    """Choose GPU with unified memory support"""
    log_message("Detected GPUs:")
    for gpu in gpus:
        unified = "✓UM" if gpu["unified_memory_capable"] else "✗UM"
        log_message(f"[{gpu['index']}] {gpu['name']} {unified} VRAM: {gpu['vram']}MB")
    time.sleep(1)
    
    suitable_gpus = [g for g in gpus if g["unified_memory_capable"] and g["vram"] >= MIN_VRAM]
    if not suitable_gpus:
        log_message("No suitable GPUs", "ERROR")
        time.sleep(3)
        raise InstallerError("No GPUs with unified memory support")
    
    if len(suitable_gpus) == 1:
        log_message(f"Auto-selected GPU: {suitable_gpus[0]['index']}")
        time.sleep(1)
        return suitable_gpus[0]['index']
    
    best_gpu = max(suitable_gpus, key=lambda x: x["vram"])
    log_message(f"Auto-selected GPU: {best_gpu['index']}")
    time.sleep(1)
    return best_gpu['index']

def check_system() -> Tuple[bool, Dict[str, any]]:
    """Check system requirements"""
    system_info = {
        "cuda_version": None,
        "driver_version": None,
        "gpus": [],
        "selected_gpu": 0,
        "unified_memory_support": False,
        "cuda_architectures": []
    }
    log_message("Verifying system...")
    time.sleep(1)
    if platform.system() != "Linux":
        log_message("Linux required", "ERROR")
        time.sleep(3)
        raise InstallerError("Linux required")
    try:
        with open("/etc/os-release") as f:
            os_info = f.read()
        if "ubuntu" not in os_info.lower():
            log_message("Ubuntu recommended", "WARNING")
            time.sleep(1)
    except Exception:
        log_message("OS check failed", "WARNING")
        time.sleep(1)
    try:
        smi_output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,driver_version,memory.total,name,compute_cap",
             "--format=csv,noheader,nounits"],
            text=True
        ).strip()
        if not smi_output:
            log_message("No GPUs found", "ERROR")
            time.sleep(3)
            raise InstallerError("No GPUs found")
        gpus = []
        driver_version = None
        for line in smi_output.split('\n'):
            if line.strip():
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 5:
                    gpu_index, driver_ver, vram, gpu_name, compute_cap = parts
                    if driver_version is None:
                        driver_version = driver_ver
                    gpus.append({
                        "index": int(gpu_index),
                        "vram": int(vram),
                        "name": gpu_name,
                        "compute_capability": compute_cap,
                        "unified_memory_capable": check_unified_memory_support(compute_cap)
                    })
        if not gpus:
            log_message("No valid GPUs", "ERROR")
            time.sleep(3)
            raise InstallerError("No valid GPUs")
        driver_num = float('.'.join(driver_version.split('.')[:2]))
        if driver_num < MIN_DRIVER_VERSION:
            log_message(f"Driver {MIN_DRIVER_VERSION}+ needed", "ERROR")
            time.sleep(3)
            raise InstallerError("Driver version too low")
        system_info.update({
            "driver_version": driver_version,
            "gpus": gpus,
            "unified_memory_support": any(gpu["unified_memory_capable"] for gpu in gpus)
        })
    except Exception:
        log_message("Driver check failed", "ERROR")
        time.sleep(3)
        raise InstallerError("Driver issue")
    try:
        cuda_bin = "/usr/local/cuda/bin/nvcc"
        if not os.path.exists(cuda_bin):
            log_message("CUDA missing", "ERROR")
            time.sleep(3)
            raise InstallerError("CUDA missing")
        nvcc = subprocess.check_output([cuda_bin, "--version"], text=True)
        match = re.search(r"release (\d+\.\d+)", nvcc)
        if not match:
            log_message("CUDA version error", "ERROR")
            time.sleep(3)
            raise InstallerError("CUDA version error")
        cuda_version = float(match.group(1))
        if cuda_version < MIN_CUDA_VERSION:
            log_message(f"CUDA {MIN_CUDA_VERSION}+ needed", "ERROR")
            time.sleep(3)
            raise InstallerError("CUDA too old")
        system_info["cuda_version"] = match.group(1)
        if not check_cuda_compatibility(system_info["cuda_version"], gpus):
            log_message("CUDA-GPU mismatch", "ERROR")
            time.sleep(3)
            raise InstallerError("CUDA-GPU mismatch")
    except Exception:
        log_message("CUDA check failed", "ERROR")
        time.sleep(3)
        raise InstallerError("CUDA issue")
    _, arch_string = get_cuda_architecture_flags()
    system_info["cuda_architectures"] = arch_string
    suitable_gpus = [gpu for gpu in gpus if gpu["vram"] >= MIN_VRAM]
    if not suitable_gpus:
        log_message("No sufficient VRAM", "ERROR")
        time.sleep(3)
        raise InstallerError("Insufficient VRAM")
    system_info["selected_gpu"] = select_gpu(suitable_gpus)
    return True, system_info

def configure_cuda_compilers(cuda_version: str) -> Tuple[Dict[str, str], str]:
    """Configure compatible GCC compiler for CUDA"""
    log_message("Checking compiler compatibility")
    time.sleep(1)
    
    max_gcc = CUDA_GCC_COMPATIBILITY.get(cuda_version, 9)
    log_message(f"CUDA {cuda_version} max gcc: {max_gcc}")
    time.sleep(1)
    
    gcc_path = f"/usr/bin/gcc-{max_gcc}"
    gxx_path = f"/usr/bin/g++-{max_gcc}"
    
    env = os.environ.copy()
    
    # Try to use the specific GCC version if available
    if os.path.exists(gcc_path) and os.path.exists(gxx_path):
        log_message(f"Using gcc-{max_gcc}")
        time.sleep(1)
        # Verify GCC version for debugging
        try:
            gcc_version_output = subprocess.check_output([gcc_path, "--version"], text=True, timeout=10)
            version_line = gcc_version_output.splitlines()[0]
            log_message(f"Compiler: {version_line}")
            time.sleep(1)
        except Exception as e:
            log_message(f"GCC version check failed: {e}", "WARNING")
            time.sleep(1)
        env["CC"] = gcc_path
        env["CXX"] = gxx_path
        return env, gcc_path
    
    # Fall back to system GCC
    log_message(f"gcc-{max_gcc} not found, checking system GCC", "WARNING")
    time.sleep(1)
    
    try:
        gcc_output = subprocess.check_output(["gcc", "--version"], text=True, timeout=10)
        gcc_match = re.search(r"gcc \(.*\) (\d+)\.(\d+)", gcc_output)
        
        if gcc_match:
            system_gcc_major = int(gcc_match.group(1))
            log_message(f"System GCC version: {system_gcc_major}")
            time.sleep(1)
            
            if system_gcc_major <= max_gcc:
                log_message(f"Using system gcc-{system_gcc_major}")
                time.sleep(1)
                system_gcc = shutil.which("gcc")
                system_gxx = shutil.which("g++")
                if system_gcc and system_gxx:
                    env["CC"] = system_gcc
                    env["CXX"] = system_gxx
                return env, system_gcc
            else:
                log_message(f"System gcc-{system_gcc_major} too new for CUDA {cuda_version}", "WARNING")
                log_message("CUDA compilation may fail", "WARNING")
                time.sleep(1)
                system_gcc = shutil.which("gcc")
                system_gxx = shutil.which("g++")
                if system_gcc and system_gxx:
                    env["CC"] = system_gcc
                    env["CXX"] = system_gxx
                return env, system_gcc
        else:
            log_message("Could not determine GCC version", "WARNING")
            time.sleep(1)
            system_gcc = shutil.which("gcc")
            system_gxx = shutil.which("g++")
            if system_gcc and system_gxx:
                env["CC"] = system_gcc
                env["CXX"] = system_gxx
            return env, system_gcc
    except Exception as e:
        log_message(f"GCC version check failed: {e}", "WARNING")
        time.sleep(1)
        system_gcc = shutil.which("gcc")
        system_gxx = shutil.which("g++")
        if system_gcc and system_gxx:
            env["CC"] = system_gcc
            env["CXX"] = system_gxx
        return env, system_gcc or "/usr/bin/gcc"

def install_system_deps(system_info: Dict[str, any]) -> bool:
    """Install system dependencies with improved GCC handling"""
    log_message("Installing system dependencies")
    time.sleep(1)
    
    # Check if we need sudo
    has_sudo = check_sudo_available()
    apt_cmd = ["sudo", "apt"] if has_sudo else ["apt"]
    
    # Clean up problematic repositories
    log_message("Cleaning problematic repositories")
    
    # Check for CUDA repository version mismatch and warn
    try:
        if os.path.exists("/etc/apt/sources.list.d/"):
            sources_output = subprocess.check_output(
                ["find", "/etc/apt/sources.list.d/", "-name", "*cuda*", "-type", "f"], 
                text=True
            ).strip()
            if sources_output and "ubuntu2004" in sources_output:
                log_message("CUDA repo version mismatch detected", "WARNING")
                log_message("Consider updating CUDA keyring", "WARNING")
                time.sleep(1)
    except Exception:
        pass
    
    # Install software-properties-common
    log_message("Installing software-properties-common")
    success, output = run_command(apt_cmd + ["install", "-y", "software-properties-common"], timeout=200)
    if not success:
        log_message(f"software-properties-common failed", "ERROR")
        time.sleep(3)
        return False
    log_message("software-properties-common installed")
    time.sleep(1)
    
    # Add Ubuntu Toolchain PPA for GCC
    log_message("Adding GCC Toolchain PPA")
    success, output = run_command(["sudo", "add-apt-repository", "-y", "ppa:ubuntu-toolchain-r/test"], timeout=120)
    if not success:
        log_message("PPA add failed", "WARNING")
        time.sleep(1)
    else:
        log_message("PPA added")
        time.sleep(1)
    
    # Check and enable universe repository
    log_message("Enabling universe repository")
    success, output = run_command(["sudo", "add-apt-repository", "-y", "universe"], timeout=120)
    if not success:
        log_message("Universe enable failed", "WARNING")
        time.sleep(1)
    else:
        log_message("Universe enabled")
        time.sleep(1)
    
    # Update package lists with error handling for problematic repos
    log_message("Updating package lists")
    success, output = run_command(apt_cmd + ["update"], timeout=300)
    if not success:
        # Check if it's just the toolchain PPA causing issues
        if "ubuntu-toolchain-r" in output and ("404" in output or "NO_PUBKEY" in output):
            log_message("Toolchain PPA unavailable, continuing", "WARNING")
            time.sleep(1)
        else:
            log_message("Package update failed", "ERROR")
            time.sleep(3)
            return False
    log_message("Package lists updated")
    time.sleep(1)
    
    # Check for required tools
    required_tools = ["git", "cmake", "build-essential", "libcurl4-openssl-dev"]
    log_message("Installing build tools")
    success, output = run_command(apt_cmd + ["install", "-y"] + required_tools, timeout=300)
    if not success:
        log_message("Build tools failed", "ERROR")
        time.sleep(3)
        return False
    log_message("Build tools installed")
    time.sleep(1)
    
    # Improved GCC installation strategy
    cuda_version = system_info['cuda_version']
    max_gcc = CUDA_GCC_COMPATIBILITY.get(cuda_version, 9)
    gcc_packages = [f"gcc-{max_gcc}", f"g++-{max_gcc}"]
    
    # Install specific GCC version
    log_message(f"Installing GCC {max_gcc}")
    success, output = run_command(apt_cmd + ["install", "-y"] + gcc_packages, timeout=300)
    if not success:
        log_message(f"GCC-{max_gcc} install failed", "ERROR")
        time.sleep(3)
        return False
    
    # Set GCC alternatives
    log_message("Configuring GCC alternatives")
    run_command(["sudo", "update-alternatives", "--install", "/usr/bin/gcc", "gcc", f"/usr/bin/gcc-{max_gcc}", "90"])
    run_command(["sudo", "update-alternatives", "--install", "/usr/bin/g++", "g++", f"/usr/bin/g++-{max_gcc}", "90"])
    run_command(["sudo", "update-alternatives", "--set", "gcc", f"/usr/bin/gcc-{max_gcc}"])
    run_command(["sudo", "update-alternatives", "--set", "g++", f"/usr/bin/g++-{max_gcc}"])
    
    # Verify GCC version
    try:
        gcc_output = subprocess.check_output(["gcc", "--version"], text=True, timeout=10)
        version_line = gcc_output.splitlines()[0]
        log_message(f"Active GCC: {version_line}")
        time.sleep(1)
    except Exception as e:
        log_message(f"GCC verification failed: {e}", "WARNING")
        time.sleep(1)
    
    # Verify cmake is available
    log_message("Verifying cmake installation")
    try:
        subprocess.check_output(["cmake", "--version"], timeout=10)
        log_message("CMake verified")
        time.sleep(1)
    except (subprocess.CalledProcessError, FileNotFoundError):
        log_message("CMake not found", "ERROR")
        time.sleep(3)
        return False
    
    # Install development libraries
    dev_libs = [
        "pkg-config", "libssl-dev", "zlib1g-dev", "libbz2-dev",
        "libreadline-dev", "libsqlite3-dev", "libncurses5-dev",
        "libxml2-dev", "libxmlsec1-dev", "libffi-dev", "liblzma-dev",
        "libcurl4-openssl-dev"
    ]
    log_message("Installing dev libraries")
    success, output = run_command(apt_cmd + ["install", "-y"] + dev_libs, timeout=400)
    if not success:
        log_message("Dev libraries failed", "ERROR")
        time.sleep(3)
        return False
    log_message("Dev libraries installed")
    time.sleep(1)
    
    # Install Python development
    python_deps = ["python3-dev", "python3-venv", "python3-pip"]
    log_message("Installing Python dev")
    success, output = run_command(apt_cmd + ["install", "-y"] + python_deps, timeout=200)
    if not success:
        log_message("Python dev failed", "ERROR")
        time.sleep(3)
        return False
    log_message("Python dev installed")
    time.sleep(1)
    
    # Install utilities
    utilities = ["wget", "curl", "llvm", "xz-utils", "tk-dev"]
    log_message("Installing utilities")
    success, output = run_command(apt_cmd + ["install", "-y"] + utilities, timeout=200)
    if not success:
        log_message("Utilities install failed", "ERROR")
        time.sleep(3)
        return False
    log_message("Utilities installed")
    time.sleep(1)
    
    log_message("System dependencies completed")
    time.sleep(1)
    return True

def setup_llama_cpp() -> bool:
    """Setup llama.cpp with retry logic and progress for cloning"""
    log_message("Setting up llama.cpp")
    time.sleep(1)
    
    # Redefine LLAMA_DIR for temporary cloning
    LLAMA_DIR = BASE_DIR / "data" / "temp" / "llama.cpp"
    
    def safe_rmtree(path: Path, retries: int = 3, delay: float = 1.0) -> None:
        """Safely remove directory with retries"""
        for attempt in range(retries):
            try:
                if path.exists():
                    shutil.rmtree(path, ignore_errors=False)
                return
            except OSError as e:
                if attempt < retries - 1:
                    log_message(f"Cleanup retry {attempt + 1}/{retries}: {e}", "WARNING")
                    time.sleep(delay)
                else:
                    raise
    
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            # Check if valid repo exists
            git_dir = LLAMA_DIR / ".git"
            makefile = LLAMA_DIR / "Makefile"
            cmake_file = LLAMA_DIR / "CMakeLists.txt"
            
            if git_dir.exists() and makefile.exists() and cmake_file.exists():
                log_message("Valid llama.cpp found")
                # Try to fetch updates
                success, output = run_command([
                    "git", "fetch", "--depth", "1", "origin", "master"
                ], cwd=LLAMA_DIR, timeout=120)
                
                if success:
                    success, output = run_command([
                        "git", "reset", "--hard", "origin/master"
                    ], cwd=LLAMA_DIR, timeout=30)
                    if success:
                        log_message("Llama.cpp updated")
                        time.sleep(1)
                        return True
                log_message("Update failed, re-cloning")
            
            # Clean incomplete directory
            if LLAMA_DIR.exists():
                log_message("Removing incomplete clone")
                safe_rmtree(LLAMA_DIR)
                time.sleep(1)
            
            # Clone with simplified progress
            log_message(f"Cloning attempt {retry_count + 1}/{max_retries}")
            LLAMA_DIR.parent.mkdir(parents=True, exist_ok=True)
            
            clone_cmd = [
                "git", "clone", "--progress",
                "--depth", "1", "--single-branch", "--branch", "master",
                "https://github.com/ggerganov/llama.cpp.git",
                str(LLAMA_DIR)
            ]
            
            # Simple progress tracking
            start_time = time.time()
            estimated_size = 50000  # KB
            
            process = subprocess.Popen(
                clone_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            # Monitor process with simple progress updates
            while process.poll() is None:
                elapsed = time.time() - start_time
                # Simple time-based progress estimation
                progress = min(elapsed / 60.0, 0.95)  # Assume 60s max, cap at 95%
                estimated_downloaded = progress * estimated_size
                estimated_time = 60.0
                
                display_progress(progress, estimated_downloaded, estimated_size, elapsed, estimated_time)
                time.sleep(0.5)
            
            return_code = process.wait()
            
            # Complete progress bar
            elapsed = time.time() - start_time
            display_progress(1.0, estimated_size, estimated_size, elapsed, elapsed)
            sys.stdout.write("\n")  # New line after progress
            sys.stdout.flush()
            
            if return_code == 0:
                if makefile.exists() and cmake_file.exists():
                    log_message("Clone complete")
                    time.sleep(1)
                    return True
                else:
                    log_message("Clone incomplete", "WARNING")
                    safe_rmtree(LLAMA_DIR)
                    time.sleep(1)
            else:
                stderr_output = process.stderr.read() if process.stderr else "Unknown error"
                log_message(f"Clone failed: {stderr_output[:100]}", "WARNING")
                safe_rmtree(LLAMA_DIR)
                time.sleep(1)
            
            retry_count += 1
            if retry_count < max_retries:
                wait_time = 5 * retry_count
                log_message(f"Retrying in {wait_time}s")
                time.sleep(wait_time)
        
        except Exception as e:
            log_message(f"Clone error: {e}", "ERROR")
            safe_rmtree(LLAMA_DIR)
            retry_count += 1
            if retry_count < max_retries:
                wait_time = 5 * retry_count
                log_message(f"Retry {retry_count + 1}/{max_retries}")
                time.sleep(wait_time)
    
    log_message("Clone attempts failed", "ERROR")
    time.sleep(3)
    raise InstallerError("Failed to clone llama.cpp")

def compile_llama_cpp(cuda_version: str, arch_string: str) -> bool:
    """Compile llama.cpp with detailed feedback"""
    log_message("Compiling llama.cpp...")
    time.sleep(1)
    
    # Redefine LLAMA_DIR and BUILD_DIR for temporary cloning
    LLAMA_DIR = BASE_DIR / "data" / "temp" / "llama.cpp"
    BUILD_DIR = LLAMA_DIR / "build"
    
    def safe_rmtree(path: Path, retries: int = 3, delay: float = 1.0) -> None:
        """Safely remove directory with retries"""
        for attempt in range(retries):
            try:
                if path.exists():
                    shutil.rmtree(path, ignore_errors=False)
                return
            except OSError as e:
                if attempt < retries - 1:
                    log_message(f"Cleanup retry {attempt + 1}/{retries}: {e}", "WARNING")
                    time.sleep(delay)
                else:
                    raise
    
    # Clean build directory
    if BUILD_DIR.exists():
        log_message("Cleaning build directory")
        safe_rmtree(BUILD_DIR)
        time.sleep(1)
    
    BUILD_DIR.mkdir(parents=True)
    
    # Verify CMakeLists.txt exists
    if not (LLAMA_DIR / "CMakeLists.txt").exists():
        log_message(f"CMakeLists.txt missing in {LLAMA_DIR}", "ERROR")
        time.sleep(3)
        raise InstallerError("CMakeLists.txt not found")
    
    # Configure CUDA compilers
    cuda_env, gcc_path = configure_cuda_compilers(cuda_version)
    
    # Create patch for __builtin_assume
    patch_h = BUILD_DIR / "patch.h"
    with open(patch_h, "w") as f:
        f.write("#ifndef PATCH_H\n")
        f.write("#define PATCH_H\n")
        f.write("#ifdef __builtin_assume\n")
        f.write("#undef __builtin_assume\n")
        f.write("#endif\n")
        f.write("#define __builtin_assume(x) ((void)0)\n")
        f.write("#endif\n")
    log_message("Applied patch for __builtin_assume")
    time.sleep(1)
    
    # Prepare cmake flags with explicit host compiler and patch
    cmake_flags = [
        "-DGGML_CUDA=ON",
        "-DGGML_CUDA_UNIFIED_MEMORY=ON",
        "-DGGML_CUDA_F16=ON",
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DCMAKE_CUDA_ARCHITECTURES={arch_string}",
        f"-DCMAKE_CUDA_HOST_COMPILER={gcc_path}",
        f"-DCMAKE_CUDA_FLAGS:STRING=-include {str(patch_h.resolve())}"
    ]
    
    # Set CUDA environment
    cuda_env.update({"CUDACXX": "/usr/local/cuda/bin/nvcc"})
    
    # Configure with CMake
    log_message("Configuring CMake build")
    success, output = run_command(
        ["cmake", str(LLAMA_DIR)] + cmake_flags,
        cwd=BUILD_DIR,
        timeout=180,
        env=cuda_env
    )
    if not success:
        log_message(f"CMake failed: {output}", "ERROR")
        time.sleep(3)
        raise InstallerError("CMake configuration failed")
    log_message("CMake configured successfully")
    time.sleep(1)
    
    # Compile binary - build all targets for maximum coverage
    log_message("Compiling binary...")
    cpu_count = min(os.cpu_count() or 4, 8)
    success, output = run_command(
        ["make", "-j", str(cpu_count), "all"],
        cwd=BUILD_DIR,
        timeout=900,
        env=cuda_env
    )
    log_message(f"Make output: {output}", "INFO")  # Log full make output
    if not success:
        log_message(f"Make failed: {output}", "ERROR")
        time.sleep(3)
        raise InstallerError("Binary compilation failed")
    log_message("Binary compiled successfully")
    time.sleep(1)
    
    # Install binary - flexible search for executables
    log_message("Installing binary...")
    possible_locations = [
        BUILD_DIR / "bin" / "main",
        BUILD_DIR / "main",
        BUILD_DIR / "bin" / "llama",
        BUILD_DIR / "llama",
        BUILD_DIR / "bin" / "llama-cli",
        BUILD_DIR / "llama-cli",
    ]
    llama_binary = next((loc for loc in possible_locations if loc.exists()), None)
    
    if not llama_binary:
        # Log directory contents for debugging
        log_message("Searching for executables in build directory...", "WARNING")
        build_files = [f for f in BUILD_DIR.rglob("*") if f.is_file() and os.access(f, os.X_OK)]
        if build_files:
            log_message("Found executables:", "WARNING")
            for f in build_files:
                log_message(str(f), "WARNING")
            # Use the first executable found as a fallback
            llama_binary = build_files[0]
            log_message(f"Using fallback binary: {llama_binary}", "WARNING")
        else:
            log_message("Build directory contents:", "WARNING")
            for item in BUILD_DIR.rglob("*"):
                log_message(str(item), "WARNING")
            log_message("No executables found", "ERROR")
            time.sleep(3)
            raise InstallerError("Compiled binary missing")
    
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(llama_binary, BIN_DIR / "llama")
    os.chmod(BIN_DIR / "llama", 0o755)
    log_message(f"Binary installed from {llama_binary} to {BIN_DIR / 'llama'}")
    time.sleep(1)
    
    # Test binary
    log_message("Testing binary...")
    success, output = run_command([str(BIN_DIR / "llama"), "--help"], timeout=10)
    if not success:
        log_message(f"Binary test failed: {output}", "WARNING")
        time.sleep(1)
    else:
        log_message("Binary test passed")
        time.sleep(1)
    
    return True

def create_config(system_info: Dict[str, any]) -> None:
    """Generate config file"""
    log_message("Creating config...")
    time.sleep(1)
    
    # Clean up existing data directory
    data_dir = BASE_DIR / "data"
    if data_dir.exists():
        log_message("Removing existing data")
        shutil.rmtree(data_dir)
        time.sleep(1)
    
    # Calculate optimal batch size
    gpu = next(g for g in system_info["gpus"] if g["index"] == system_info["selected_gpu"])
    vram_mb = gpu["vram"]
    if vram_mb >= 12288:
        n_batch = 8192
    elif vram_mb >= 8192:
        n_batch = 4096
    elif vram_mb >= 4096:
        n_batch = 2048
    else:
        n_batch = 1024

    # Create directories
    log_message("Creating data directories")
    for dir_path in [BASE_DIR / "data", BASE_DIR / "data" / "temp", 
                     BASE_DIR / "data" / "history", BASE_DIR / "data" / "vectors"]:
        dir_path.mkdir(parents=True, exist_ok=True)
    log_message("Data directories created")
    time.sleep(1)

    # Write configuration
    log_message("Writing config file")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        f.write(CONFIG_TEMPLATE % (
            n_batch,
            json.dumps(system_info["gpus"]),
            system_info["selected_gpu"],
            system_info["cuda_version"],
            gpu["compute_capability"]
        ))
    log_message("Config file written")
    time.sleep(1)

    # Verify configuration
    if not CONFIG_PATH.exists():
        log_message("Config creation failed", "ERROR")
        time.sleep(3)
        raise InstallerError("Config creation failed")

    try:
        with open(CONFIG_PATH, "r") as f:
            json.load(f)
        log_message("Config verified")
        time.sleep(1)
    except json.JSONDecodeError:
        log_message("Invalid config JSON", "ERROR")
        time.sleep(3)
        raise InstallerError("Invalid JSON config")

def setup_python_env(cuda_version: str, arch_string: str) -> bool:
    """Setup Python environment with detailed feedback"""
    log_message("Creating Python env...")
    time.sleep(1)
    
    # Clean up existing venv
    if VENV_DIR.exists():
        log_message("Removing existing venv")
        shutil.rmtree(VENV_DIR)
        time.sleep(1)
    
    # Create virtual environment
    log_message("Creating virtual env")
    success, output = run_command([sys.executable, "-m", "venv", str(VENV_DIR)])
    if not success:
        log_message(f"Venv failed: {output}", "ERROR")
        time.sleep(3)
        raise InstallerError("Venv creation failed")
    log_message("Virtual env created")
    time.sleep(1)
    
    # Upgrade pip
    pip = str(VENV_DIR / "bin" / "pip")
    log_message("Upgrading pip...")
    success, output = run_command([pip, "install", "--upgrade", "pip", "wheel", "setuptools"], timeout=120)
    if not success:
        log_message(f"Pip upgrade failed: {output}", "ERROR")
        time.sleep(3)
        raise InstallerError("Pip upgrade failed")
    log_message("Pip upgraded successfully")
    time.sleep(1)
    
    # Install basic requirements
    basic_reqs = [req for req in REQUIREMENTS if not req.startswith("llama-cpp-python")]
    log_message("Installing basic packages")
    success, output = run_command([pip, "install"] + basic_reqs, timeout=600)
    if not success:
        log_message(f"Basic packages failed: {output}", "ERROR")
        time.sleep(3)
        raise InstallerError("Basic packages failed")
    log_message("Basic packages installed")
    time.sleep(1)
    
    # Install llama-cpp-python with CUDA
    log_message("Compiling llama-cpp-python")
    env, gcc_path = configure_cuda_compilers(cuda_version)
    env.update({
        "LLAMA_CUDA": "1",
        "CMAKE_ARGS": f"-DLLAMA_CUDA=ON -DLLAMA_CUDA_UNIFIED_MEMORY=ON -DCMAKE_CUDA_ARCHITECTURES={arch_string} -DCMAKE_CUDA_HOST_COMPILER={gcc_path}",
        "FORCE_CMAKE": "1",
        "CUDACXX": "/usr/local/cuda/bin/nvcc"
    })
    success, output = run_command([
        pip, "install", "--force-reinstall", "--no-cache-dir", "llama-cpp-python==0.2.23"
    ], timeout=1200, env=env)
    if not success:
        log_message(f"llama-cpp-python failed: {output}", "ERROR")
        time.sleep(3)
        raise InstallerError("llama-cpp-python failed")
    log_message("llama-cpp-python installed")
    time.sleep(1)
    
    log_message("Python env complete")
    time.sleep(1)
    return True


def print_system_summary(system_info: Dict[str, any]) -> None:
    """Display system summary"""
    log_message("System Summary:")
    log_message(f"CUDA: {system_info['cuda_version']}")
    log_message(f"Driver: {system_info['driver_version']}")
    log_message(f"GPU: {system_info['selected_gpu']}")
    time.sleep(1)

def cleanup_on_failure():
    """Remove failed setup"""
    log_message("Cleaning up...")
    time.sleep(1)
    for path in [VENV_DIR, LLAMA_DIR, BUILD_DIR, BIN_DIR]:
        if path.exists():
            shutil.rmtree(path)

def main():
    """Run installer"""
    try:
        log_message(f"Installing {APP_NAME}")
        time.sleep(1)
        
        sys_ok, system_info = check_system()
        print_system_summary(system_info)
        
        if not install_system_deps(system_info):
            raise InstallerError("System dependencies failed")
        
        setup_llama_cpp()
        compile_llama_cpp(system_info['cuda_version'], system_info['cuda_architectures'])
        setup_python_env(system_info['cuda_version'], system_info['cuda_architectures'])
        create_config(system_info)
        
        log_message("Install complete")
        log_message(f"Run: {VENV_DIR}/bin/python launcher.py")
        
    except InstallerError as e:
        log_message(f"Failed: {e}", "ERROR")
        cleanup_on_failure()
        sys.exit(1)
    except Exception as e:
        log_message(f"Error: {e}", "ERROR")
        cleanup_on_failure()
        sys.exit(1)

if __name__ == "__main__":
    main()