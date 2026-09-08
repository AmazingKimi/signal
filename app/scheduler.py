"""Background scheduling (0.5 §17/18/19) — macOS launchd + Windows Task Scheduler.

- Only registered after the user explicitly confirms (Enable background monitoring?).
- Enable / Disable / Pause are fully user-controllable.
- Honest: a powered-off machine does not monitor (local product, no cloud).
- Windows: two daily tasks at 08:00 and 20:00 via schtasks.exe.
- Linux: intentionally unsupported — status reports UNAVAILABLE instead of
  pretending that a task was registered.
"""
import os
import platform
import subprocess
import sys

PLIST_NAME = "com.kimi.opportunity-monitor.plist"
LAUNCH_AGENTS = os.path.expanduser("~/Library/LaunchAgents")
PLIST_PATH = os.path.join(LAUNCH_AGENTS, PLIST_NAME)

# Windows Task Scheduler task names (08:00 and 20:00 run).
WIN_TASK_MORNING = "KimiOpportunityMonitor-0800"
WIN_TASK_EVENING = "KimiOpportunityMonitor-2000"
_WIN_TIMES = (("08:00", WIN_TASK_MORNING), ("20:00", WIN_TASK_EVENING))

# 每日 08:00 和 20:00 各跑一次 monitor_runner（WEEKLY 的 watch 在脚本内判断星期几）
_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.kimi.opportunity-monitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{runner}</string>
    </array>
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Hour</key><integer>20</integer><key>Minute</key><integer>0</integer></dict>
    </array>
    <key>StandardOutPath</key>
    <string>{home}/.kimi-monitor.log</string>
    <key>StandardErrorPath</key>
    <string>{home}/.kimi-monitor.log</string>
</dict>
</plist>
"""


def _app_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _runner() -> str:
    return os.path.join(_app_dir(), "monitor_runner.py")


# ================================================================ macOS (launchd)

def _mac_registered() -> bool:
    return os.path.exists(PLIST_PATH)


def _mac_enable() -> dict:
    os.makedirs(LAUNCH_AGENTS, exist_ok=True)
    plist = _PLIST_TEMPLATE.format(
        python=sys.executable, runner=_runner(), home=os.path.expanduser("~"),
    )
    with open(PLIST_PATH, "w") as f:
        f.write(plist)
    subprocess.run(["launchctl", "unload", PLIST_PATH], capture_output=True)
    r = subprocess.run(["launchctl", "load", PLIST_PATH], capture_output=True)
    return {"registered": True, "loaded": r.returncode == 0,
            "plist": PLIST_PATH, "schedule": ["08:00", "20:00"],
            "note": "Mac 开机且联网时，每天 08:00、20:00 自动检查 ACTIVE Watches"}


def _mac_disable() -> dict:
    if not _mac_registered():
        return {"registered": False}
    subprocess.run(["launchctl", "unload", PLIST_PATH], capture_output=True)
    try:
        os.remove(PLIST_PATH)
    except OSError:
        pass
    return {"registered": False}


# ================================================================ Windows (schtasks)

def _win_registered() -> bool:
    return _win_task_exists(WIN_TASK_MORNING)


def _win_task_exists(task_name: str) -> bool:
    r = subprocess.run(
        ["schtasks", "/Query", "/TN", task_name],
        capture_output=True, text=True, timeout=15,
    )
    return r.returncode == 0


def _win_task_create(task_name: str, at_time: str) -> bool:
    # /TR 的引号必须传给 schtasks：外层用双引号包命令，内部 python 路径再用双引号。
    command = f'"{sys.executable}" "{_runner()}"'
    r = subprocess.run(
        ["schtasks", "/Create", "/TN", task_name, "/TR", command,
         "/SC", "DAILY", "/ST", at_time, "/F"],
        capture_output=True, text=True, timeout=20,
    )
    return r.returncode == 0


def _win_task_delete(task_name: str) -> bool:
    r = subprocess.run(
        ["schtasks", "/Delete", "/TN", task_name, "/F"],
        capture_output=True, text=True, timeout=20,
    )
    return r.returncode == 0


def _win_enable() -> dict:
    created = []
    for at_time, task_name in _WIN_TIMES:
        if _win_task_create(task_name, at_time):
            created.append(at_time)
    return {"registered": bool(created), "loaded": len(created) == len(_WIN_TIMES),
            "schedule": [t for t, _ in _WIN_TIMES],
            "tasks": [n for _, n in _WIN_TIMES],
            "note": "Windows 任务计划程序：每天 08:00、20:00 自动检查 ACTIVE Watches"}


def _win_disable() -> dict:
    for _, task_name in _WIN_TIMES:
        _win_task_delete(task_name)
    return {"registered": _win_registered()}


# ================================================================ 统一入口

def registered() -> bool:
    system = platform.system()
    if system == "Darwin":
        return _mac_registered()
    if system == "Windows":
        return _win_registered()
    return False


def enable() -> dict:
    system = platform.system()
    if system == "Darwin":
        return _mac_enable()
    if system == "Windows":
        return _win_enable()
    return {"registered": False, "loaded": False, "schedule": ["08:00", "20:00"],
            "note": "自动调度仅支持 macOS (launchd) 与 Windows (Task Scheduler)；当前平台不注册任务"}


def disable() -> dict:
    system = platform.system()
    if system == "Darwin":
        return _mac_disable()
    if system == "Windows":
        return _win_disable()
    return {"registered": False}


def status() -> dict:
    system = platform.system()
    if system == "Darwin":
        return {"registered": _mac_registered(),
                "plist": PLIST_PATH if _mac_registered() else None,
                "platform": system, "schedule": ["08:00", "20:00"]}
    if system == "Windows":
        return {"registered": _win_registered(),
                "tasks": [n for _, n in _WIN_TIMES if _win_task_exists(n)],
                "platform": system, "schedule": ["08:00", "20:00"]}
    return {"registered": False, "platform": system, "schedule": ["08:00", "20:00"],
            "note": "自动调度仅支持 macOS 与 Windows"}
