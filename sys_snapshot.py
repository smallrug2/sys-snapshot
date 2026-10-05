"""sys_snapshot.py - One-shot cross-platform system snapshot.

Purpose: print a concise snapshot (OS, CPU model/count/percent, RAM,
    disk usage per mount, top-5 processes by CPU, boot time). Optionally
    emit JSON (--json) and/or save to a file (--save).

Usage:
    python sys_snapshot.py
    python sys_snapshot.py --json --save snapshot.json

Platform: Windows + Linux. psutil is optional (richer numbers when
    present); without it the script degrades to stdlib shims and marks
    unavailable fields as SKIP instead of crashing.
"""
import argparse
import datetime
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    psutil = None
    HAS_PSUTIL = False


def cpu_model():
    if platform.system() == "Windows":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System"
                                r"\CentralProcessor\0") as k:
                return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    elif platform.system() == "Darwin":  # macOS: sysctl brand string
        try:
            r = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                               capture_output=True, text=True, timeout=10)
            if r.stdout.strip():
                return r.stdout.strip()
        except OSError:
            pass
    else:  # Linux: first "model name" in /proc/cpuinfo
        try:
            with open("/proc/cpuinfo", encoding="utf-8",
                      errors="ignore") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.processor() or platform.machine() or "SKIP:unknown"


def top_processes(n=5):
    if HAS_PSUTIL:
        try:
            procs = []
            for p in psutil.process_iter(["pid", "name"]):
                try:
                    procs.append((p.cpu_percent(interval=0.1),
                                  p.info["pid"], p.info.get("name") or "?"))
                except Exception:
                    pass
            procs.sort(reverse=True)
            return [{"cpu_pct": c, "pid": pid, "name": name}
                    for c, pid, name in procs[:n]]
        except Exception as e:
            return [{"error": str(e)[:100]}]
    print("SKIP: per-process CPU needs psutil", file=sys.stderr)
    return [{"note": "SKIP:no-psutil"}]


def disks():
    out = []
    if HAS_PSUTIL:
        try:
            for part in psutil.disk_partitions(all=False):
                try:
                    u = psutil.disk_usage(part.mountpoint)
                    out.append({"mount": part.mountpoint, "fstype": part.fstype,
                                "total_gb": round(u.total / 1e9, 2),
                                "used_gb": round(u.used / 1e9, 2),
                                "pct": u.percent})
                except OSError:
                    pass
            return out
        except Exception:
            pass
    # stdlib fallback: just the current drive.
    anchor = Path.cwd().anchor or "/"
    try:
        u = shutil.disk_usage(anchor)
        out.append({"mount": anchor, "fstype": "SKIP:no-psutil",
                    "total_gb": round(u.total / 1e9, 2),
                    "used_gb": round(u.used / 1e9, 2),
                    "pct": round(u.used / u.total * 100, 1)})
    except OSError as e:
        out.append({"error": str(e)[:100]})
    return out


def collect():
    snap = {"os": "%s %s (%s)" % (platform.system(), platform.release(),
                                  platform.version()),
            "hostname": platform.node(),
            "cpu_model": cpu_model(),
            "cpu_count": os.cpu_count()}
    if HAS_PSUTIL:
        try:
            snap["cpu_percent"] = psutil.cpu_percent(interval=0.5)
        except Exception:
            snap["cpu_percent"] = "SKIP:error"
        try:
            m = psutil.virtual_memory()
            snap["ram"] = {"total_gb": round(m.total / 1e9, 2),
                           "available_gb": round(m.available / 1e9, 2),
                           "pct": m.percent}
        except Exception:
            snap["ram"] = "SKIP:error"
        try:
            b = psutil.boot_time()
            snap["boot_time"] = datetime.datetime.fromtimestamp(b).isoformat(
                timespec="seconds")
        except Exception:
            snap["boot_time"] = "SKIP:error"
    else:
        snap["cpu_percent"] = "SKIP:no-psutil"
        snap["ram"] = "SKIP:no-psutil"
        snap["boot_time"] = "SKIP:no-psutil"
    snap["disks"] = disks()
    snap["top_processes"] = top_processes(5)
    snap["psutil"] = HAS_PSUTIL
    return snap


def render_text(snap):
    lines = ["== sys_snapshot %s ==" % snap["hostname"],
             "OS:        %s" % snap["os"],
             "CPU:       %s x%s (%.1f%%)" % (
                 snap["cpu_model"], snap["cpu_count"],
                 snap["cpu_percent"] if isinstance(snap.get("cpu_percent"),
                                                   (int, float)) else -1)
             if isinstance(snap.get("cpu_percent"), (int, float))
             else "CPU:       %s x%s (%s)" % (snap["cpu_model"],
                                             snap["cpu_count"],
                                             snap["cpu_percent"]),
             "RAM:       %s" % snap["ram"],
             "Boot:      %s" % snap["boot_time"]]
    lines.append("Disks:")
    for d in snap["disks"]:
        if "error" in d:
            lines.append("  ERR %s" % d["error"])
        else:
            lines.append("  %s [%s] %.2f/%.2f GB (%s%%)" % (
                d["mount"], d["fstype"], d["used_gb"], d["total_gb"], d["pct"]))
    lines.append("Top-5 by CPU:")
    for p in snap["top_processes"]:
        if "error" in p or "note" in p:
            lines.append("  %s" % (p.get("error") or p.get("note")))
        else:
            lines.append("  %5.1f%%  pid=%s  %s" % (p["cpu_pct"], p["pid"],
                                                   p["name"]))
    return "\n".join(lines)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="One-shot system snapshot")
    p.add_argument("--json", action="store_true", help="Emit JSON")
    p.add_argument("--save", default="", help="Save output to FILE")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    snap = collect()
    out = json.dumps(snap, indent=2) if args.json else render_text(snap)
    print(out)
    if args.save:
        Path(args.save).write_text(out + "\n", encoding="utf-8")
        print("saved", args.save)


if __name__ == "__main__":
    main()
