#!/usr/bin/env python3
"""OSRacer demo GUI.

This GUI runs on the Orin Nano itself. It does not change firmware control
priority: RC remains available as an emergency override; the demo only publishes
ROS /cmd_vel commands and stop commands.
"""

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

DEFAULT_WS = os.environ.get("OSRACER_WS", str(Path.home() / "osracer_ws"))
DEFAULT_PORT = os.environ.get("OSRACER_PORT", "/dev/osrbot_base")
DEFAULT_BAUD = os.environ.get("OSRACER_BAUD", "460800")


def resolve_runtime() -> tuple[Path, Path, bool]:
    try:
        from ament_index_python.packages import get_package_share_directory

        share_dir = Path(get_package_share_directory("osracer_demo"))
        return share_dir, share_dir / "scripts", True
    except Exception:
        root = Path(__file__).resolve().parents[1]
        return root, root / "ros_demo" / "scripts", False


ROOT, SCRIPTS, PACKAGE_MODE = resolve_runtime()


def bash_env_prefix() -> str:
    return (
        "set +u; "
        "source /opt/ros/humble/setup.bash; "
        f"if [ -f {DEFAULT_WS}/install/setup.bash ]; then source {DEFAULT_WS}/install/setup.bash; fi; "
        "set -u; "
    )


def script_cmd(name: str) -> str:
    return str(SCRIPTS / name)


def motion_cmd(demo: str) -> str:
    if PACKAGE_MODE:
        return f"ros2 run osracer_demo drive_demo {demo} --yes"
    return f"cd {ROOT} && python3 ros_demo/scripts/drive_demo.py {demo} --yes"


class LeaderDemo(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("OSRacer 演示控制台")
        self.geometry("980x760")
        self.minsize(900, 700)

        self.proc_lock = threading.Lock()
        self.processes: dict[str, subprocess.Popen] = {}
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.advanced_names = {"odom-rviz", "mapping", "navigation", "active-mapping", "slam-navigation"}
        self.motion_prefix = "motion-"
        self.status_vars = {
            "vehicle": tk.StringVar(value="未检查"),
            "ros": tk.StringVar(value="未检查"),
            "control": tk.StringVar(value="ROS 模式，遥控器保留急停/接管"),
            "action": tk.StringVar(value="空闲"),
        }

        self._build_ui()
        self.after(100, self._drain_logs)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        bg = "#f3f4f6"
        panel_bg = "#ffffff"
        text = "#111827"
        muted = "#6b7280"
        border = "#d1d5db"
        accent = "#2563eb"
        danger = "#dc2626"
        dark = "#111827"

        self.configure(bg=bg)
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Primary.TButton", font=("Arial", 13, "bold"), padding=(14, 10))
        style.configure("Action.TButton", font=("Arial", 12), padding=(12, 9))
        style.configure("Stop.TButton", font=("Arial", 18, "bold"), padding=(16, 14))
        style.map("Primary.TButton", foreground=[("active", "#ffffff")], background=[("active", "#1d4ed8")])
        style.configure("Primary.TButton", foreground="#ffffff", background=accent, bordercolor=accent)
        style.configure("Stop.TButton", foreground="#ffffff", background=danger, bordercolor=danger)
        style.map("Stop.TButton", foreground=[("active", "#ffffff")], background=[("active", "#b91c1c")])

        header = tk.Frame(self, bg=dark, padx=24, pady=18)
        header.pack(fill="x")
        title_block = tk.Frame(header, bg=dark)
        title_block.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_block,
            text="OSRacer 演示控制台",
            bg=dark,
            fg="#ffffff",
            font=("Arial", 22, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_block,
            text="现场低速演示 / 建图 / 导航控制",
            bg=dark,
            fg="#cbd5e1",
            font=("Arial", 12),
        ).pack(anchor="w", pady=(4, 0))
        tk.Label(
            header,
            text=f"{DEFAULT_PORT}  |  {DEFAULT_BAUD} baud",
            bg="#1f2937",
            fg="#e5e7eb",
            font=("Arial", 12, "bold"),
            padx=14,
            pady=8,
        ).pack(side="right")

        status = tk.Frame(self, bg=bg, padx=16, pady=14)
        status.pack(fill="x")
        for idx, (name, label) in enumerate([
            ("vehicle", "车辆连接"),
            ("ros", "ROS 环境"),
            ("control", "控制模式"),
            ("action", "当前动作"),
        ]):
            card = tk.Frame(status, bg=panel_bg, highlightbackground=border, highlightthickness=1, padx=14, pady=10)
            card.grid(row=0, column=idx, sticky="ew", padx=(0, 10))
            status.columnconfigure(idx, weight=1)
            tk.Label(card, text=label, bg=panel_bg, fg=muted, font=("Arial", 10, "bold")).pack(anchor="w")
            tk.Label(card, textvariable=self.status_vars[name], bg=panel_bg, fg=text, font=("Arial", 13, "bold")).pack(anchor="w", pady=(4, 0))

        main = tk.Frame(self, bg=bg, padx=16)
        main.pack(fill="x", pady=(0, 12))

        def section(parent: tk.Widget, title: str) -> tk.Frame:
            outer = tk.Frame(parent, bg=panel_bg, highlightbackground=border, highlightthickness=1, padx=14, pady=12)
            tk.Label(outer, text=title, bg=panel_bg, fg=text, font=("Arial", 14, "bold")).pack(anchor="w", pady=(0, 8))
            return outer

        left = section(main, "基础流程")
        middle = section(main, "动作演示")
        right = section(main, "高级功能")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        middle.grid(row=0, column=1, sticky="nsew", padx=(0, 10))
        right.grid(row=0, column=2, sticky="nsew")
        for idx in range(3):
            main.columnconfigure(idx, weight=1, uniform="main")

        buttons = [
            ("状态检查", self.check_status),
            ("启动车辆链路", self.start_vehicle_link),
        ]
        for text, command in buttons:
            ttk.Button(left, text=text, command=command, style="Primary.TButton").pack(fill="x", pady=4)
        tk.Label(
            left,
            text="先检查环境，再启动底盘链路。遥控器仍保留现场接管价值。",
            bg=panel_bg,
            fg=muted,
            wraplength=270,
            justify="left",
            font=("Arial", 11),
        ).pack(anchor="w", pady=(10, 0))

        motion_buttons = [
            ("暖机小动作", lambda: self.run_motion("warmup")),
            ("直线+S弯展示", lambda: self.run_motion("showcase")),
            ("完整 8 字演示", lambda: self.run_motion("figure8")),
            ("持续最小圈绕行", lambda: self.run_motion("circle")),
        ]
        for text, command in motion_buttons:
            ttk.Button(middle, text=text, command=command, style="Action.TButton").pack(fill="x", pady=4)
        ttk.Button(middle, text="紧急停车", command=self.emergency_stop, style="Stop.TButton").pack(fill="x", pady=(14, 4))

        advanced = [
            ("打开里程计 RViz", self.open_odom_rviz),
            ("打开建图演示", self.start_mapping),
            ("打开导航演示", self.start_navigation),
            ("边走边建图", self.start_active_mapping),
            ("边建图边导航", self.start_slam_navigation),
            ("停止高级节点", self.stop_advanced),
        ]
        for text, command in advanced:
            ttk.Button(right, text=text, command=command, style="Action.TButton").pack(fill="x", pady=4)

        note = (
            "高级功能只负责启动节点和 RViz，不自动下发导航目标。切换高级功能前先停止高级节点。"
        )
        tk.Label(right, text=note, bg=panel_bg, fg=muted, wraplength=270, justify="left", font=("Arial", 11)).pack(fill="x", pady=(10, 0))

        log_frame = tk.Frame(self, bg=panel_bg, highlightbackground=border, highlightthickness=1, padx=12, pady=10)
        log_frame.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        log_tools = tk.Frame(log_frame, bg=panel_bg)
        log_tools.pack(fill="x", pady=(0, 6))
        tk.Label(log_tools, text="运行日志", bg=panel_bg, fg=text, font=("Arial", 14, "bold")).pack(side="left")
        ttk.Button(log_tools, text="清空日志", command=self.clear_log, style="Action.TButton").pack(side="right")

        self.log_text = tk.Text(
            log_frame,
            height=12,
            wrap="word",
            bg="#0f172a",
            fg="#e5e7eb",
            insertbackground="#e5e7eb",
            relief="flat",
            font=("Menlo", 11),
            padx=10,
            pady=10,
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=log_scroll.set)

    def clear_log(self) -> None:
        self.log_text.delete("1.0", "end")

    def log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_queue.put(f"[{stamp}] {text}")

    def _drain_logs(self) -> None:
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert("end", line + "\n")
            self.log_text.see("end")
        self.after(100, self._drain_logs)

    def run_shell(self, command: str, name: str | None = None, keep: bool = False) -> subprocess.Popen | None:
        if name:
            with self.proc_lock:
                existing = self.processes.get(name)
            if existing is not None and existing.poll() is None:
                self.log(f"{name} 已在运行，跳过重复启动")
                return existing

        full = bash_env_prefix() + command
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

        threading.Thread(target=self._watch_process, args=(proc, name, keep), daemon=True).start()
        return proc

    def running_process_names(self) -> list[str]:
        with self.proc_lock:
            return [
                name for name, proc in self.processes.items()
                if proc.poll() is None
            ]

    def start_advanced_once(self, name: str, label: str, command: str) -> None:
        running_advanced = [
            proc_name for proc_name in self.running_process_names()
            if proc_name in self.advanced_names
        ]
        if running_advanced:
            if name in running_advanced:
                self.log(f"{label} 已在启动或运行，跳过重复点击")
            else:
                self.log("已有高级功能正在启动或运行，请先点“停止高级节点”再切换。")
                messagebox.showinfo("高级功能正在运行", "请先点“停止高级节点”，等 2-3 秒后再启动另一个高级功能。")
            return

        self.status_vars["action"].set(label)
        self.run_shell(command, name=name, keep=True)

    def terminate_processes(self, names: set[str] | None = None, prefix: str | None = None) -> None:
        with self.proc_lock:
            selected = [
                (name, proc) for name, proc in self.processes.items()
                if (names is not None and name in names) or (prefix is not None and name.startswith(prefix))
            ]
        for name, proc in selected:
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    self.log(f"已停止 {name}")
                except Exception as exc:
                    self.log(f"停止 {name} 失败: {exc}")
            with self.proc_lock:
                if self.processes.get(name) is proc:
                    self.processes.pop(name, None)

    def _watch_process(self, proc: subprocess.Popen, name: str | None, keep: bool) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            self.log(line.rstrip())
        code = proc.wait()
        if name:
            with self.proc_lock:
                if self.processes.get(name) is proc:
                    self.processes.pop(name, None)
        if name:
            self.log(f"{name} 已退出 code={code}")

    def check_status(self) -> None:
        self.status_vars["action"].set("状态检查")
        serial_exists = Path(DEFAULT_PORT).exists()
        setup_exists = Path(DEFAULT_WS, "install", "setup.bash").exists()
        self.status_vars["vehicle"].set("串口存在" if serial_exists else "串口未找到")
        self.status_vars["ros"].set("工作区 OK" if setup_exists else "工作区未找到")
        self.log(f"车辆串口 {DEFAULT_PORT}: {'OK' if serial_exists else 'MISSING'}")
        self.log(f"ROS 工作区 {DEFAULT_WS}: {'OK' if setup_exists else 'MISSING'}")
        self.log("遥控器无需开机才能 ROS 演示；底层遥控器仍保留急停/接管用途。")
        self.log("如果遥控器关闭时看到 RC failsafe，这不是 ROS 演示阻塞条件。")
        self.run_shell("ros2 topic list | head -40", keep=False)

    def start_vehicle_link(self) -> None:
        self.status_vars["action"].set("启动车辆链路")
        if "vehicle" in self.processes and self.processes["vehicle"].poll() is None:
            self.log("车辆链路已在运行")
            return
        cmd = script_cmd("start_basic_demo.sh")
        self.run_shell(cmd, name="vehicle", keep=True)
        self.status_vars["vehicle"].set("启动中")

    def run_motion(self, demo: str) -> None:
        running_motion = [
            name for name in self.running_process_names()
            if name.startswith(self.motion_prefix)
        ]
        if running_motion:
            messagebox.showinfo("动作正在执行", "当前已有动作正在执行，请先等待结束或点击紧急停车。")
            return

        if demo == "warmup":
            label = "暖机小动作"
        elif demo == "showcase":
            label = "直线+S弯展示"
        elif demo == "figure8":
            label = "完整 8 字演示"
        elif demo == "circle":
            label = "持续最小圈绕行"
        else:
            label = demo
        extra = "\n\n该动作会一直运行，直到点击“紧急停车”。" if demo == "circle" else ""
        if not messagebox.askokcancel("开始动作", f"确认场地安全后开始：{label}{extra}"):
            return
        self.status_vars["action"].set(label)
        cmd = motion_cmd(demo)
        if demo == "figure8":
            cmd += " --loops 1 --duration 14.0 --speed 0.55"
        self.run_shell(cmd, name=f"motion-{demo}")

    def emergency_stop(self) -> None:
        self.status_vars["action"].set("紧急停车")
        self.log("发送紧急停车")
        self.run_shell(script_cmd("stop_all_demo.sh"), name="stop")
        self.terminate_processes(names=self.advanced_names, prefix=self.motion_prefix)

    def open_odom_rviz(self) -> None:
        self.start_advanced_once(
            "odom-rviz",
            "打开里程计 RViz",
            script_cmd("open_odom_rviz.sh"),
        )

    def start_mapping(self) -> None:
        self.start_advanced_once(
            "mapping",
            "建图演示",
            script_cmd("start_mapping_demo.sh"),
        )

    def start_navigation(self) -> None:
        self.start_advanced_once(
            "navigation",
            "导航演示",
            script_cmd("start_navigation_demo.sh"),
        )

    def start_active_mapping(self) -> None:
        self.start_advanced_once(
            "active-mapping",
            "边走边建图",
            script_cmd("start_active_mapping_demo.sh"),
        )

    def start_slam_navigation(self) -> None:
        self.start_advanced_once(
            "slam-navigation",
            "边建图边导航",
            script_cmd("start_slam_navigation_demo.sh"),
        )

    def stop_advanced(self) -> None:
        self.log("停止高级节点")
        self.run_shell(script_cmd("stop_all_demo.sh"), name="stop-advanced")
        self.terminate_processes(names=self.advanced_names)

    def on_close(self) -> None:
        if messagebox.askokcancel("退出", "退出前会发送停车命令并停止由本界面启动的进程。"):
            self.emergency_stop()
            with self.proc_lock:
                procs = list(self.processes.values())
            for proc in procs:
                if proc.poll() is None:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except Exception:
                        pass
            self.after(600, self.destroy)


def main() -> int:
    app = LeaderDemo()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
