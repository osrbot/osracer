#!/usr/bin/env python3
"""Tkinter control panel for local OSRacer demos."""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ament_index_python.packages import get_package_share_directory


DEFAULT_PORT = os.environ.get("OSRACER_PORT", "/dev/osrbot_base")
DEFAULT_BAUD = os.environ.get("OSRACER_BAUD", "460800")
DEFAULT_WS = os.environ.get("OSRACER_WS", str(Path.home() / "osracer_ws"))


def shell_prefix() -> str:
    return (
        "set +u; "
        "source /opt/ros/humble/setup.bash; "
        f"if [ -f {DEFAULT_WS}/install/setup.bash ]; then source {DEFAULT_WS}/install/setup.bash; fi; "
        "set -u; "
    )


class LeaderDemo(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("OSRacer 演示控制台")
        self.geometry("980x720")
        self.minsize(900, 640)

        share_dir = Path(get_package_share_directory("osracer_demo"))
        self.scripts_dir = share_dir / "scripts"
        self.proc_lock = threading.Lock()
        self.processes: dict[str, subprocess.Popen] = {}
        self.log_queue: queue.Queue[str] = queue.Queue()

        self.status_vars = {
            "vehicle": tk.StringVar(value="未检查"),
            "ros": tk.StringVar(value="未检查"),
            "action": tk.StringVar(value="空闲"),
        }

        self._build_ui()
        self.after(100, self._drain_logs)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.configure("Title.TLabel", font=("Arial", 20, "bold"))
        style.configure("Big.TButton", font=("Arial", 13), padding=8)
        style.configure("Stop.TButton", font=("Arial", 16, "bold"), padding=10)

        header = ttk.Frame(self, padding=14)
        header.pack(fill="x")
        ttk.Label(header, text="OSRacer 演示控制台", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=f"{DEFAULT_PORT} @ {DEFAULT_BAUD}").pack(side="right")

        status = ttk.LabelFrame(self, text="状态", padding=10)
        status.pack(fill="x", padx=14, pady=(0, 10))
        for idx, (key, label) in enumerate((("vehicle", "车辆"), ("ros", "ROS"), ("action", "当前动作"))):
            ttk.Label(status, text=f"{label}:").grid(row=0, column=idx * 2, sticky="w", padx=(0, 4))
            ttk.Label(status, textvariable=self.status_vars[key]).grid(row=0, column=idx * 2 + 1, sticky="w", padx=(0, 24))

        buttons = ttk.Frame(self, padding=(14, 0, 14, 10))
        buttons.pack(fill="x")
        left = ttk.LabelFrame(buttons, text="基础流程", padding=10)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        right = ttk.LabelFrame(buttons, text="动作演示", padding=10)
        right.pack(side="right", fill="both", expand=True, padx=(6, 0))

        for label, command in (
            ("状态检查", self.check_status),
            ("启动车辆链路", self.start_chassis),
            ("观察里程计", self.watch_odom),
        ):
            ttk.Button(left, text=label, command=command, style="Big.TButton").pack(fill="x", pady=4)
        ttk.Button(left, text="紧急停车", command=self.emergency_stop, style="Stop.TButton").pack(fill="x", pady=(16, 4))

        for label, demo in (
            ("暖机小动作", "warmup"),
            ("直线低速前进", "straight"),
            ("左缓弯", "left"),
            ("右缓弯", "right"),
            ("8 字演示", "figure8"),
            ("巡航演示", "patrol"),
        ):
            ttk.Button(right, text=label, command=lambda d=demo: self.run_motion(d), style="Big.TButton").pack(fill="x", pady=4)

        advanced = ttk.LabelFrame(self, text="高级演示", padding=10)
        advanced.pack(fill="x", padx=14, pady=(0, 10))
        for label, script, name in (
            ("基础 RViz", "start_basic_demo.sh", "advanced-basic"),
            ("建图演示", "start_mapping_demo.sh", "advanced-mapping"),
            ("导航演示", "start_navigation_demo.sh", "advanced-navigation"),
            ("边走边建图", "start_active_mapping_demo.sh", "advanced-active-mapping"),
            ("边建图边导航", "start_slam_navigation_demo.sh", "advanced-slam-navigation"),
            ("停止全部演示", "stop_all_demo.sh", "advanced-stop"),
        ):
            ttk.Button(
                advanced,
                text=label,
                command=lambda s=script, n=name: self.run_script(s, n),
                style="Big.TButton",
            ).pack(side="left", fill="x", expand=True, padx=4)

        log_frame = ttk.LabelFrame(self, text="日志", padding=8)
        log_frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.log_text = tk.Text(log_frame, height=12, wrap="word")
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)

    def log(self, text: str) -> None:
        self.log_queue.put(f"[{time.strftime('%H:%M:%S')}] {text}")

    def _drain_logs(self) -> None:
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert("end", line + "\n")
            self.log_text.see("end")
        self.after(100, self._drain_logs)

    def run_shell(self, command: str, name: str | None = None) -> subprocess.Popen | None:
        if name:
            with self.proc_lock:
                existing = self.processes.get(name)
            if existing is not None and existing.poll() is None:
                self.log(f"{name} 已在运行")
                return existing

        full = shell_prefix() + command
        self.log(f"$ {command}")
        try:
            proc = subprocess.Popen(
                ["bash", "-lc", full],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                preexec_fn=os.setsid,
            )
        except Exception as exc:
            self.log(f"启动失败: {exc}")
            return None

        if name:
            with self.proc_lock:
                self.processes[name] = proc
        threading.Thread(target=self._watch_process, args=(proc, name), daemon=True).start()
        return proc

    def _watch_process(self, proc: subprocess.Popen, name: str | None) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            self.log(line.rstrip())
        code = proc.wait()
        if name:
            with self.proc_lock:
                if self.processes.get(name) is proc:
                    self.processes.pop(name, None)
            self.log(f"{name} 已退出 code={code}")

    def check_status(self) -> None:
        self.status_vars["action"].set("状态检查")
        self.status_vars["vehicle"].set("串口存在" if Path(DEFAULT_PORT).exists() else "串口未找到")
        self.status_vars["ros"].set("工作区存在" if Path(DEFAULT_WS, "install", "setup.bash").exists() else "工作区未找到")
        self.run_shell(f"{self.scripts_dir}/check_osracer.sh {DEFAULT_PORT}", name="check")

    def start_chassis(self) -> None:
        self.status_vars["action"].set("启动车辆链路")
        self.run_shell(f"{self.scripts_dir}/start_chassis.sh {DEFAULT_PORT}", name="chassis")

    def watch_odom(self) -> None:
        self.status_vars["action"].set("观察里程计")
        self.run_shell("ros2 run osracer_demo odom_watch", name="odom-watch")

    def run_script(self, script_name: str, process_name: str) -> None:
        if script_name != "stop_all_demo.sh":
            running = [name for name in self.running_process_names() if name.startswith("advanced-")]
            if running:
                messagebox.showinfo("高级演示", "请先点击“停止全部演示”，再启动另一个高级功能。")
                return
        self.status_vars["action"].set(process_name)
        self.run_shell(f"{self.scripts_dir}/{script_name}", name=process_name)

    def running_process_names(self) -> list[str]:
        with self.proc_lock:
            return [name for name, proc in self.processes.items() if proc.poll() is None]

    def run_motion(self, demo: str) -> None:
        if demo != "stop" and not messagebox.askokcancel("开始动作", f"确认场地安全后开始：{demo}"):
            return
        self.status_vars["action"].set(demo)
        extra = " --loops 1" if demo == "figure8" else ""
        self.run_shell(f"ros2 run osracer_demo drive_demo {demo} --yes{extra}", name=f"motion-{demo}")

    def emergency_stop(self) -> None:
        self.status_vars["action"].set("停车")
        self.run_shell(f"{self.scripts_dir}/stop_all_demo.sh", name="stop")
        self.terminate_motion()
        self.terminate_advanced()

    def terminate_motion(self) -> None:
        with self.proc_lock:
            selected = [(name, proc) for name, proc in self.processes.items() if name.startswith("motion-")]
        for name, proc in selected:
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    self.log(f"已停止 {name}")
                except Exception as exc:
                    self.log(f"停止 {name} 失败: {exc}")

    def terminate_advanced(self) -> None:
        with self.proc_lock:
            selected = [(name, proc) for name, proc in self.processes.items() if name.startswith("advanced-")]
        for name, proc in selected:
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    self.log(f"已停止 {name}")
                except Exception as exc:
                    self.log(f"停止 {name} 失败: {exc}")

    def on_close(self) -> None:
        if not messagebox.askokcancel("退出", "退出前会发送停车命令并关闭演示进程，确认退出？"):
            return
        self.emergency_stop()
        with self.proc_lock:
            procs = list(self.processes.values())
        for proc in procs:
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except Exception:
                    pass
        self.after(500, self.destroy)


def main() -> int:
    app = LeaderDemo()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
