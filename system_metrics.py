"""Capability-based local Linux metrics. No GTK or compositor dependencies."""
from __future__ import annotations
import csv
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

def system_install_date() -> datetime | None:
    """Estimate the OS install date from the oldest available pacman log entry."""
    path = Path("/var/log/pacman.log")
    try:
        with path.open(encoding="utf-8", errors="replace") as log_file:
            for line in log_file:
                match = re.match(r"\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(?::\d{2})?(?:[+-]\d{4})?\]", line)
                if match:
                    try:
                        return datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M")
                    except ValueError:
                        continue
    except OSError:
        pass
    return None


def load_system_health_history(path: Path) -> dict[str, dict[str, dict[str, float | int]]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    days = payload.get("days") if isinstance(payload, dict) else None
    if not isinstance(days, dict):
        return {}
    cleaned = {}
    for day, metrics in days.items():
        if not isinstance(day, str) or not isinstance(metrics, dict):
            continue
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            continue
        clean_metrics = {}
        for key in ("cpu", "memory", "gpu"):
            value = metrics.get(key)
            if not isinstance(value, dict):
                continue
            total, count = value.get("total"), value.get("count")
            if (isinstance(total, (int, float)) and math.isfinite(total)
                    and isinstance(count, int) and count > 0):
                clean_metrics[key] = {"total": float(total), "count": count}
        if clean_metrics:
            cleaned[day] = clean_metrics
    return dict(sorted(cleaned.items())[-30:])


def save_system_health_history(path: Path, days: dict[str, dict[str, dict[str, float | int]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = {"version": 1, "days": dict(sorted(days.items())[-30:])}
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def uptime_text() -> str:
    try:
        seconds = int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return "—"
    days, remainder = divmod(seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes = remainder // 60
    return f"{days}d {hours}h" if days else f"{hours}h {minutes}m"


def human_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{amount:.1f} TB"


def history_window_seconds(refresh_seconds: int, sample_limit: int = 90) -> int:
    return max(1, int(refresh_seconds)) * max(1, int(sample_limit))


def history_window_label(refresh_seconds: int, sample_limit: int = 90) -> str:
    seconds = history_window_seconds(refresh_seconds, sample_limit)
    if seconds < 60:
        return f"{seconds} sec"
    minutes = seconds / 60
    return f"{int(minutes)} min" if minutes.is_integer() else f"{minutes:.1f} min"


def recent_history_samples(samples, window_seconds: int, now: float | None = None):
    """Keep timestamped samples whose measurements still fall in the live window."""
    cutoff = (time.monotonic() if now is None else now) - max(1, window_seconds)
    return [(sample_time, value) for sample_time, value in samples if sample_time >= cutoff]


def read_memory() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            match = re.match(r"(MemTotal|MemAvailable|MemFree|Buffers|Cached|SwapTotal|SwapFree):\s+(\d+)", line)
            if match:
                values[match.group(1)] = int(match.group(2)) * 1024
    except OSError:
        pass
    return values


def cpu_snapshot() -> tuple[int, int] | None:
    try:
        fields = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        numbers = [int(x) for x in fields]
        idle = numbers[3] + (numbers[4] if len(numbers) > 4 else 0)
        return sum(numbers), idle
    except (OSError, ValueError, IndexError):
        return None


def cpu_identity(path: Path = Path("/proc/cpuinfo")) -> tuple[str, int]:
    """Read processor model and logical CPU count once instead of every refresh."""
    model = "Unknown processor"
    processors = 0
    try:
        for line in path.read_text(errors="replace").splitlines():
            lowered = line.lower()
            if model == "Unknown processor" and lowered.startswith(("model name", "hardware")) and ":" in line:
                model = line.split(":", 1)[1].strip() or model
            if re.match(r"^processor\s*:", lowered):
                processors += 1
    except OSError:
        pass
    return model, processors or os.cpu_count() or 0


def cpu_core_snapshot(path: Path = Path("/proc/stat")) -> dict[str, tuple[int, int]]:
    cores = {}
    try:
        for line in path.read_text().splitlines():
            fields = line.split()
            if not fields or not re.fullmatch(r"cpu\d+", fields[0]) or len(fields) < 5:
                continue
            values = [int(value) for value in fields[1:]]
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            cores[fields[0]] = (sum(values), idle)
    except (OSError, ValueError):
        return {}
    return cores


def cpu_usage_between(previous: tuple[int, int] | None, current: tuple[int, int] | None) -> int | None:
    if not previous or not current:
        return None
    total_delta = current[0] - previous[0]
    idle_delta = current[1] - previous[1]
    return max(0, min(100, round((1 - idle_delta / total_delta) * 100))) if total_delta > 0 else None


def hardware_sensor_readings(hwmon_root: Path = Path("/sys/class/hwmon"), thermal_root: Path = Path("/sys/class/thermal")) -> list[tuple[str, str]]:
    """Read only sensor values exposed by Linux hwmon and thermal sysfs."""
    readings: list[tuple[str, str]] = []
    try:
        devices = sorted(hwmon_root.glob("hwmon*"))
    except OSError:
        devices = []
    for device in devices:
        try:
            device_name = (device / "name").read_text().strip() or device.name
        except OSError:
            device_name = device.name
        for input_path in device.glob("temp*_input"):
            suffix = input_path.name.removeprefix("temp").removesuffix("_input")
            try:
                celsius = int(input_path.read_text().strip()) / 1000
                if not -100 <= celsius <= 250:
                    continue
                label_path = device / f"temp{suffix}_label"
                label = label_path.read_text().strip() if label_path.exists() else f"Temperature {suffix}"
                readings.append((f"{device_name} · {label}", f"{celsius:.1f} °C"))
            except (OSError, ValueError):
                continue
        for input_path in device.glob("fan*_input"):
            suffix = input_path.name.removeprefix("fan").removesuffix("_input")
            try:
                rpm = int(input_path.read_text().strip())
                if rpm < 0:
                    continue
                label_path = device / f"fan{suffix}_label"
                label = label_path.read_text().strip() if label_path.exists() else f"Fan {suffix}"
                readings.append((f"{device_name} · {label}", f"{rpm} RPM"))
            except (OSError, ValueError):
                continue
    try:
        zones = sorted(thermal_root.glob("thermal_zone*"))
    except OSError:
        zones = []
    for zone in zones:
        try:
            celsius = int((zone / "temp").read_text().strip()) / 1000
            if not -100 <= celsius <= 250:
                continue
            label = (zone / "type").read_text().strip() or zone.name
            readings.append((f"Thermal · {label}", f"{celsius:.1f} °C"))
        except (OSError, ValueError):
            continue
    return readings


def _read_nvidia_smi_metrics() -> list[dict[str, object]]:
    """Read NVIDIA values through the vendor CLI when its driver is available."""
    if not shutil.which("nvidia-smi"):
        return []
    query = (
        "name,utilization.gpu,memory.used,memory.total,clocks.gr,"
        "power.draw,temperature.gpu"
    )
    try:
        result = subprocess.run(
            ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []

    def number(value, integer=False):
        value = value.strip()
        if not value or value.casefold() in {"n/a", "[not supported]"}:
            return None
        try:
            parsed = float(value)
            if not math.isfinite(parsed) or parsed < 0:
                return None
            return int(parsed) if integer else parsed
        except ValueError:
            return None

    metrics = []
    try:
        rows = csv.reader(result.stdout.splitlines(), skipinitialspace=True)
        for index, row in enumerate(rows):
            if len(row) != 7:
                continue
            name, utilization, memory_used, memory_total, clock, power, temperature = row
            gpu_utilization = number(utilization, integer=True)
            if gpu_utilization is not None and gpu_utilization > 100:
                gpu_utilization = None
            temp_c = number(temperature)
            if temp_c is not None and temp_c > 250:
                temp_c = None
            memory_used_mib = number(memory_used)
            memory_total_mib = number(memory_total)
            metrics.append({
                "card": f"nvidia-smi-{index}",
                "vendor": "NVIDIA",
                "model": name.strip(),
                "utilization": gpu_utilization,
                "vram_used": round(memory_used_mib * 1024 * 1024) if memory_used_mib is not None else None,
                "vram_total": round(memory_total_mib * 1024 * 1024) if memory_total_mib is not None else None,
                "clock": number(clock, integer=True),
                "power_watts": number(power),
                "sensors": [("GPU temperature", f"{temp_c:.1f} °C")] if temp_c is not None else [],
            })
    except (csv.Error, ValueError):
        return []
    return metrics


def read_gpu_metrics(drm_root: Path = Path("/sys/class/drm")) -> list[dict[str, object]]:
    """Read GPU values exposed by DRM and driver-owned sysfs files."""
    vendors = {"0x1002": "AMD", "0x10de": "NVIDIA", "0x8086": "Intel"}
    results = []
    try:
        cards = [path for path in sorted(drm_root.glob("card[0-9]*")) if re.fullmatch(r"card\d+", path.name)]
    except OSError:
        cards = []

    def read_number(path):
        try:
            return int(path.read_text().strip(), 0)
        except (OSError, ValueError):
            return None

    for card in cards:
        device = card / "device"
        if not device.exists():
            continue
        try:
            vendor_id = (device / "vendor").read_text().strip().lower()
        except OSError:
            vendor_id = ""
        try:
            device_id = (device / "device").read_text().strip().lower()
        except OSError:
            device_id = ""
        try:
            product = (device / "product_name").read_text().strip()
        except OSError:
            product = ""
        metrics = {
            "card": card.name,
            "vendor": vendors.get(vendor_id, f"GPU {vendor_id}" if vendor_id else card.name),
            "model": product or (f"PCI {device_id}" if device_id else ""),
            "utilization": read_number(device / "gpu_busy_percent"),
            "vram_used": read_number(device / "mem_info_vram_used"),
            "vram_total": read_number(device / "mem_info_vram_total"),
            "clock": None,
            "power_watts": None,
            "sensors": [],
        }
        # Intel Xe publishes read-only actual GT frequencies per tile and GT.
        # Use the resolved value when available; cur_freq is only the requested
        # value and can differ from what the hardware is actually running.
        if vendor_id == "0x8086":
            actual_frequencies = []
            for frequency_path in sorted(device.glob("tile*/gt*/freq0/act_freq")):
                frequency = read_number(frequency_path)
                if frequency is not None and frequency > 0:
                    actual_frequencies.append(frequency)
            if actual_frequencies:
                # A single card-level clock keeps the existing UI compact. For
                # multi-tile devices show the fastest active GT frequency.
                metrics["clock"] = max(actual_frequencies)
        try:
            active = next(line for line in (device / "pp_dpm_sclk").read_text().splitlines() if "*" in line)
            clock = re.search(r"(\d+)\s*mhz", active, re.IGNORECASE)
            if clock:
                metrics["clock"] = int(clock.group(1))
        except (OSError, StopIteration):
            pass
        hwmon_root = device / "hwmon"
        hwmon_devices = sorted(hwmon_root.glob("hwmon*"))
        metrics["sensors"] = hardware_sensor_readings(hwmon_root, Path("/nonexistent-leaf-thermal"))
        for hwmon in hwmon_devices:
            power = read_number(hwmon / "power1_average")
            if power is None:
                power = read_number(hwmon / "power1_input")
            if power is not None and power >= 0:
                metrics["power_watts"] = power / 1_000_000
                break
        results.append(metrics)

    nvidia_cards = [gpu for gpu in results if gpu["vendor"] == "NVIDIA"]
    # Avoid probing nvidia-smi on AMD/Intel-only systems. Its query runs in the
    # existing hardware worker with a short timeout, keeping GTK responsive.
    if nvidia_cards or not cards:
        reported_nvidia = _read_nvidia_smi_metrics()
        for index, reported in enumerate(reported_nvidia):
            if index >= len(nvidia_cards):
                results.append(reported)
                continue
            gpu = nvidia_cards[index]
            for field in ("model", "utilization", "vram_used", "vram_total", "clock", "power_watts"):
                if reported[field] is not None:
                    gpu[field] = reported[field]
            gpu["sensors"].extend(
                sensor for sensor in reported["sensors"] if sensor not in gpu["sensors"]
            )
    return results


def network_snapshot() -> tuple[int, int] | None:
    interfaces = network_interface_snapshot()
    if interfaces is None or not interfaces:
        return None
    return (sum(counters[0] for counters in interfaces.values()),
            sum(counters[1] for counters in interfaces.values()))


def process_snapshot(previous: dict[int, int], elapsed: float, cpu_count: int,
                     proc_root: Path = Path("/proc")) -> tuple[list[tuple[int, str, float, int]], dict[int, int]]:
    """Return processes ranked by recent CPU use and their latest CPU counters."""
    current = {}
    rows = []
    ticks_per_second = os.sysconf("SC_CLK_TCK")
    page_size = os.sysconf("SC_PAGE_SIZE")
    try:
        entries = proc_root.iterdir()
    except OSError:
        return [], current
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            close = stat.rfind(")")
            fields = stat[close + 2:].split()
            ticks = int(fields[11]) + int(fields[12])
            rss_bytes = max(0, int(fields[21])) * page_size
            pid = int(entry.name)
            current[pid] = ticks
            name = (entry / "comm").read_text().strip() or stat[stat.find("(") + 1:close]
            delta = max(0, ticks - previous.get(pid, ticks))
            cpu = delta / ticks_per_second / max(elapsed, 0.001) / max(cpu_count, 1) * 100
            rows.append((pid, name, cpu, rss_bytes))
        except (OSError, ValueError, IndexError):
            continue
    rows.sort(key=lambda row: (row[2], row[3]), reverse=True)
    return rows[:12], current


def network_interface_snapshot(path: Path = Path("/proc/net/dev")) -> dict[str, tuple[int, int]] | None:
    """Return per-interface received/sent byte counters, excluding loopback."""
    interfaces = {}
    try:
        for line in path.read_text().splitlines()[2:]:
            if ":" not in line:
                continue
            interface, counters = line.split(":", 1)
            interface = interface.strip()
            if interface == "lo":
                continue
            fields = counters.split()
            if len(fields) >= 9:
                interfaces[interface] = (int(fields[0]), int(fields[8]))
    except (OSError, ValueError):
        return None
    return interfaces


def disk_io_snapshot() -> tuple[int, int] | None:
    """Return aggregate bytes read/written by whole block devices."""
    try:
        devices = {path.name for path in Path("/sys/block").iterdir()}
        if not devices:
            return None
        read_sectors = written_sectors = 0
        for line in Path("/proc/diskstats").read_text().splitlines():
            fields = line.split()
            if len(fields) >= 10 and fields[2] in devices:
                read_sectors += int(fields[5])
                written_sectors += int(fields[9])
        return read_sectors * 512, written_sectors * 512
    except (OSError, ValueError, IndexError):
        return None


def mounted_filesystems() -> list[tuple[str, str, int, int, int]]:
    """Return device-backed mount usage, keeping devices distinct from mounts."""
    mounts = []
    seen = set()
    try:
        lines = Path("/proc/self/mounts").read_text().splitlines()
    except OSError:
        return mounts
    for line in lines:
        fields = line.split()
        if len(fields) < 3 or not fields[0].startswith("/dev/"):
            continue
        decode = lambda value: re.sub(r"\\(040|011|012|134)", lambda m: chr(int(m.group(1), 8)), value)
        device, mountpoint = decode(fields[0]), decode(fields[1])
        if (device, mountpoint) in seen:
            continue
        seen.add((device, mountpoint))
        try:
            usage = shutil.disk_usage(mountpoint)
            mounts.append((device, mountpoint, usage.total, usage.used, usage.free))
        except OSError:
            continue
    return mounts


def group_storage_mounts(volumes: list[tuple[str, str, int, int, int]]) -> list[tuple[str, list[str], int, int]]:
    """Group mount paths that report the same device and capacity."""
    groups = {}
    for device, mountpoint, total, used, _free in volumes:
        key = (device, total, used)
        groups.setdefault(key, []).append(mountpoint)
    return [(device, mountpoints, total, used)
            for (device, total, used), mountpoints in groups.items()]


def storage_device_inventory(volumes: list[tuple[str, str, int, int, int]]) -> list[dict[str, object]]:
    """Combine physical block device capacity with mounted filesystem usage."""
    devices = {}
    sys_block = Path("/sys/block")
    for entry in sorted(sys_block.iterdir()) if sys_block.exists() else ():
        name = entry.name
        if name.startswith(("loop", "ram", "zram")):
            continue
        try:
            raw_total = int((entry / "size").read_text().strip()) * 512
        except (OSError, ValueError):
            raw_total = 0
        model = ""
        for info_path in (entry / "device/model", entry / "device/name"):
            try:
                model = info_path.read_text().strip()
                if model:
                    break
            except OSError:
                pass
        try:
            rotational = (entry / "queue/rotational").read_text().strip() == "1"
        except OSError:
            rotational = False
        kind = "hdd" if rotational else ("nvme" if name.startswith("nvme") else "ssd")
        devices[name] = {
            "name": name,
            "kind": kind,
            "model": model or ("NVMe drive" if kind == "nvme" else "Hard disk" if kind == "hdd" else "Solid state drive"),
            "total": raw_total,
            "used": 0,
            "mounts": [],
            "mounted": False,
            "filesystems": set(),
        }

    def physical_parent(device_name: str) -> str | None:
        block_path = Path("/sys/class/block") / device_name
        if not block_path.exists():
            return None
        try:
            real_path = block_path.resolve()
        except OSError:
            return None
        if (block_path / "partition").exists():
            return real_path.parent.name
        if device_name.startswith("dm-"):
            slaves = block_path / "slaves"
            try:
                for slave in slaves.iterdir():
                    parent = physical_parent(slave.name)
                    if parent:
                        return parent
            except OSError:
                pass
        return real_path.name

    grouped_volumes = group_storage_mounts(volumes)
    for source, mountpoints, total, used in grouped_volumes:
        try:
            source_name = Path(source).resolve().name
        except OSError:
            source_name = Path(source).name
        parent = physical_parent(source_name)
        key = parent if parent in devices else None
        if key is None:
            key = source
            devices.setdefault(key, {
                "name": source_name,
                "kind": "ssd" if source_name.startswith("nvme") else "hdd" if source_name.startswith("sd") else "drive",
                "model": source_name or "Storage device",
                "total": 0,
                "used": 0,
                "mounts": [],
                "mounted": False,
                "filesystems": set(),
            })
        item = devices[key]
        item["mounted"] = True
        item["mounts"].extend(mountpoints)
        filesystem_id = (source, total, used)
        if filesystem_id not in item["filesystems"]:
            item["filesystems"].add(filesystem_id)
            if item["total"] <= 0:
                item["total"] += total
            item["used"] += used
    result = []
    for item in devices.values():
        item["mounts"] = list(dict.fromkeys(item["mounts"]))
        if not item["total"]:
            continue
        result.append(item)
    result.sort(key=lambda item: (not item["mounted"], item["kind"], item["name"]))
    return result


def human_rate(value: float) -> str:
    return f"{human_bytes(max(0, int(value)))}/s"


