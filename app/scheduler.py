"""Mac launchd 调度（0.5 第 17/18/19 节）。

- 只在用户明确确认后注册（Enable background monitoring on this Mac?）
- 提供 Enable / Disable / Pause，用户完全可控
- 诚实：Mac 关机 = 不监控（本地产品，不做云）
"""
import os
import subprocess
import sys

PLIST_NAME = "com.kimi.opportunity-monitor.plist"
LAUNCH_AGENTS = os.path.expanduser("~/Library/LaunchAgents")
PLIST_PATH = os.path.join(LAUNCH_AGENTS, PLIST_NAME)

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


def registered() -> bool:
    return os.path.exists(PLIST_PATH)


def enable() -> dict:
    """注册 launchd 每日任务（用户确认后调用）。"""
    os.makedirs(LAUNCH_AGENTS, exist_ok=True)
    runner = os.path.join(_app_dir(), "monitor_runner.py")
    plist = _PLIST_TEMPLATE.format(
        python=sys.executable, runner=runner, home=os.path.expanduser("~"),
    )
    with open(PLIST_PATH, "w") as f:
        f.write(plist)
    # 卸载旧任务再加载（幂等）
    subprocess.run(["launchctl", "unload", PLIST_PATH], capture_output=True)
    r = subprocess.run(["launchctl", "load", PLIST_PATH], capture_output=True)
    return {"registered": True, "loaded": r.returncode == 0,
            "plist": PLIST_PATH, "schedule": ["08:00", "20:00"],
            "note": "Mac 开机且联网时，每天 08:00、20:00 自动检查 ACTIVE Watches"}


def disable() -> dict:
    """卸载 launchd 任务（Disable）。"""
    if not registered():
        return {"registered": False}
    subprocess.run(["launchctl", "unload", PLIST_PATH], capture_output=True)
    try:
        os.remove(PLIST_PATH)
    except OSError:
        pass
    return {"registered": False}


def status() -> dict:
    return {"registered": registered(), "plist": PLIST_PATH if registered() else None,
            "schedule": ["08:00", "20:00"]}
