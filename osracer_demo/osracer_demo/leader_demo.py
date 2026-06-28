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
        self.title("OSRacer Demo Console")
        self.geometry("980x760")
        self.minsize(900, 700)

        self.proc_lock = threading.Lock()
        self.processes: dict[str, subprocess.Popen] = {}
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.advanced_names = {"odom-rviz", "mapping", "navigation", "active-gmapping", "active-cartographer", "slam-navigation"}
        self.motion_prefix = "motion-"
        self.mode_buttons: list[ttk.Button] = []
        self.save_buttons: list[ttk.Button] = []
        self.close_requested = False
        self.status_cards: dict[str, tk.Frame] = {}
        self.status_vars = {
            "vehicle": tk.StringVar(value="Not checked"),
            "ros": tk.StringVar(value="Not checked"),
            "control": tk.StringVar(value="ROS mode; RC keeps emergency override"),
            "action": tk.StringVar(value="Idle"),
        }

        self._build_ui()
        self.set_action("Idle")
        self.after(100, self._drain_logs)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        bg = "#f5f4ef"
        panel_bg = "#fffefa"
        text_color = "#1f2933"
        muted = "#687076"
        border = "#d8d2c6"
        accent = "#3f6f5f"
        action_bg = "#e7e2d8"
        stop_bg = "#8a4b38"
        dark = "#233029"

        self.configure(bg=bg)
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Primary.TButton", font=("Arial", 13, "bold"), padding=(14, 10))
        style.configure("Action.TButton", font=("Arial", 12), padding=(12, 9))
        style.configure("Stop.TButton", font=("Arial", 18, "bold"), padding=(16, 14))
        style.map("Primary.TButton", foreground=[("active", "#ffffff")], background=[("active", "#345f50")])
        style.configure("Primary.TButton", foreground="#ffffff", background=accent, bordercolor=accent)
        style.configure("Action.TButton", foreground=text_color, background=action_bg, bordercolor=border)
        style.map("Action.TButton", background=[("active", "#ddd6c8")])
        style.configure("Stop.TButton", foreground="#ffffff", background=stop_bg, bordercolor=stop_bg)
        style.map("Stop.TButton", foreground=[("active", "#ffffff")], background=[("active", "#723d2e")])

        header = tk.Frame(self, bg=dark, padx=24, pady=18)
        header.pack(fill="x")
        title_block = tk.Frame(header, bg=dark)
        title_block.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_block,
            text="OSRacer Demo Console",
            bg=dark,
            fg="#ffffff",
            font=("Arial", 22, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_block,
            text="Field demo / mapping / navigation",
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
            ("vehicle", "Vehicle Link"),
            ("ros", "ROS Environment"),
            ("control", "Control Mode"),
            ("action", "Current Action"),
        ]):
            card = tk.Frame(status, bg=panel_bg, highlightbackground=border, highlightthickness=1, padx=14, pady=10)
            card.grid(row=0, column=idx, sticky="ew", padx=(0, 10))
            self.status_cards[name] = card
            status.columnconfigure(idx, weight=1)
            tk.Label(card, text=label, bg=panel_bg, fg=muted, font=("Arial", 10, "bold")).pack(anchor="w")
            tk.Label(card, textvariable=self.status_vars[name], bg=panel_bg, fg=text_color, font=("Arial", 13, "bold")).pack(anchor="w", pady=(4, 0))

        main = tk.Frame(self, bg=bg, padx=16)
        main.pack(fill="x", pady=(0, 12))

        def section(parent: tk.Widget, title: str) -> tk.Frame:
            outer = tk.Frame(parent, bg=panel_bg, highlightbackground=border, highlightthickness=1, padx=14, pady=12)
            tk.Label(outer, text=title, bg=panel_bg, fg=text_color, font=("Arial", 14, "bold")).pack(anchor="w", pady=(0, 8))
            return outer

        left = section(main, "Basic Flow")
        middle = section(main, "Motion Demo")
        right = section(main, "Advanced")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        middle.grid(row=0, column=1, sticky="nsew", padx=(0, 10))
        right.grid(row=0, column=2, sticky="nsew")
        for idx in range(3):
            main.columnconfigure(idx, weight=1, uniform="main")

        buttons = [
            ("Check Status", self.check_status),
            ("Start Vehicle Link", self.start_vehicle_link),
        ]
        for label_text, command in buttons:
            ttk.Button(left, text=label_text, command=command, style="Primary.TButton").pack(fill="x", pady=4)
        tk.Label(
            left,
            text="Check the environment first, then start the vehicle link. RC remains available for field takeover.",
            bg=panel_bg,
            fg=muted,
            wraplength=270,
            justify="left",
            font=("Arial", 11),
        ).pack(anchor="w", pady=(10, 0))

        motion_buttons = [
            ("Warm-up Motion", lambda: self.run_motion("warmup")),
            ("Line + S Turn", lambda: self.run_motion("showcase")),
            ("Figure-8 Demo", lambda: self.run_motion("figure8")),
            ("Continuous Tight Circle", lambda: self.run_motion("circle")),
        ]
        for label_text, command in motion_buttons:
            ttk.Button(middle, text=label_text, command=command, style="Action.TButton").pack(fill="x", pady=4)
        ttk.Button(middle, text="Emergency Stop", command=self.emergency_stop, style="Stop.TButton").pack(fill="x", pady=(14, 4))

        def action_heading(text: str) -> None:
            tk.Label(right, text=text, bg=panel_bg, fg=muted, font=("Arial", 10, "bold")).pack(anchor="w", pady=(8, 0))

        def action_row(items: list[tuple[str, object, str]]) -> None:
            row = tk.Frame(right, bg=panel_bg)
            row.pack(fill="x", pady=4)
            for idx, (label_text, command, role) in enumerate(items):
                btn = ttk.Button(row, text=label_text, command=command, style="Action.TButton")
                btn.grid(row=0, column=idx, sticky="ew", padx=(0, 6 if idx == 0 else 0))
                if role == "mode":
                    self.mode_buttons.append(btn)
                elif role == "save":
                    self.save_buttons.append(btn)
                row.columnconfigure(idx, weight=1, uniform="action")

        action_heading("View")
        action_row([("Open Odometry RViz", self.open_odom_rviz, "mode")])
        action_heading("Mapping")
        action_row([("Start Mapping", self.start_mapping, "mode"), ("Save Map", self.save_map, "save")])
        action_row([("Drive + GMapping", self.start_active_gmapping, "mode"), ("Drive + Cartographer", self.start_active_cartographer, "mode")])
        action_heading("Navigation")
        action_row([("Start Navigation", self.start_navigation, "mode")])
        action_row([("SLAM + Navigation", self.start_slam_navigation, "mode"), ("Save Cartographer Map", self.save_cartographer_map, "save")])
        action_heading("Cleanup")
        action_row([("Stop Advanced Nodes", self.stop_advanced, "cleanup")])

        note = (
            "Advanced actions only start ROS nodes and RViz. Stop advanced nodes before switching modes."
        )
        tk.Label(right, text=note, bg=panel_bg, fg=muted, wraplength=270, justify="left", font=("Arial", 11)).pack(fill="x", pady=(10, 0))

        log_frame = tk.Frame(self, bg=panel_bg, highlightbackground=border, highlightthickness=1, padx=12, pady=10)
        log_frame.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        log_tools = tk.Frame(log_frame, bg=panel_bg)
        log_tools.pack(fill="x", pady=(0, 6))
        tk.Label(log_tools, text="Runtime Log", bg=panel_bg, fg=text_color, font=("Arial", 14, "bold")).pack(side="left")
        ttk.Button(log_tools, text="Clear Log", command=self.clear_log, style="Action.TButton").pack(side="right")

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

    def set_action(self, text: str) -> None:
        self.status_vars["action"].set(text)
        lower = text.lower()
        if lower == "idle":
            bg = "#ecfdf3"
        elif "stop" in lower or "cleanup" in lower:
            bg = "#fff7ed"
        elif "error" in lower or "missing" in lower:
            bg = "#fef2f2"
        elif "saving" in lower:
            bg = "#eff6ff"
        else:
            bg = "#fffefa"
        self._set_status_card_bg("action", bg)

    def _set_status_card_bg(self, name: str, bg: str) -> None:
        card = self.status_cards.get(name)
        if card is None:
            return
        card.configure(bg=bg)
        for child in card.winfo_children():
            try:
                child.configure(bg=bg)
            except tk.TclError:
                pass

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
                self.log(f"{name} is already running; skipping duplicate start")
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
            self.log(f"Failed to start: {exc}")
            self.update_controls()
            return None

        if name:
            with self.proc_lock:
                self.processes[name] = proc

        self.update_controls()
        threading.Thread(target=self._watch_process, args=(proc, name, keep), daemon=True).start()
        return proc

    def running_process_names(self) -> list[str]:
        with self.proc_lock:
            return [
                name for name, proc in self.processes.items()
                if proc.poll() is None
            ]

    def advanced_running(self) -> bool:
        return any(name in self.advanced_names for name in self.running_process_names())

    def process_running(self, name: str) -> bool:
        with self.proc_lock:
            proc = self.processes.get(name)
        return proc is not None and proc.poll() is None

    def update_controls(self) -> None:
        mode_state = "disabled" if self.advanced_running() or self.process_running("cleanup") else "normal"
        save_state = "disabled" if self.process_running("cleanup") or self.process_running("save-map") else "normal"
        for btn in self.mode_buttons:
            btn.configure(state=mode_state)
        for btn in self.save_buttons:
            btn.configure(state=save_state)

    def _handle_process_line(self, name: str | None, line: str) -> None:
        if name != "cleanup":
            return
        if "Publishing stop commands" in line:
            self.set_action("Stopping: publishing zero speed")
        elif "Stopping tracked demo process IDs" in line:
            self.set_action("Stopping: stopping tracked nodes")
        elif "Stopping common demo ROS processes" in line:
            self.set_action("Stopping: cleaning ROS nodes")
        elif "No matching demo processes remain" in line:
            self.set_action("Stopping: verifying cleanup")

    def start_advanced_once(self, name: str, label: str, command: str) -> None:
        running_advanced = [
            proc_name for proc_name in self.running_process_names()
            if proc_name in self.advanced_names
        ]
        if running_advanced:
            if name in running_advanced:
                self.log(f"{label} is already starting or running; skipping duplicate click")
            else:
                self.log("Another advanced action is running. Stop advanced nodes before switching modes.")
                messagebox.showinfo("Advanced action running", "Press Stop Advanced Nodes, wait 2-3 seconds, then start another advanced action.")
            return

        self.set_action(label)
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
                    self.log(f"Stopped {name}")
                except Exception as exc:
                    self.log(f"Failed to stop {name}: {exc}")
            with self.proc_lock:
                if self.processes.get(name) is proc:
                    self.processes.pop(name, None)

    def _watch_process(self, proc: subprocess.Popen, name: str | None, keep: bool) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            stripped = line.rstrip()
            self.log(stripped)
            self.after(0, self._handle_process_line, name, stripped)
        code = proc.wait()
        if name:
            with self.proc_lock:
                if self.processes.get(name) is proc:
                    self.processes.pop(name, None)
        if name:
            self.log(f"{name} exited code={code}")
            self.after(0, self._after_process_exit, name, code)

    def _after_process_exit(self, name: str, code: int) -> None:
        self.update_controls()
        if name == "cleanup":
            self.set_action("Idle")
            self.status_vars["vehicle"].set("Stopped")
            self.log("Cleanup finished; console is idle.")
            if self.close_requested:
                self.destroy()
            return
        if code != 0:
            self.set_action("Error - check log")
            return
        if name == "status-check":
            self.set_action("Idle")
            return
        if name == "save-map":
            if any(proc_name in self.advanced_names for proc_name in self.running_process_names()):
                self.set_action("Mapping running")
            else:
                self.set_action("Idle")
            return
        if name.startswith(self.motion_prefix):
            self.set_action("Idle")
            return
        if name in self.advanced_names:
            if not any(proc_name in self.advanced_names for proc_name in self.running_process_names()):
                self.set_action("Idle")

    def check_status(self) -> None:
        self.set_action("Checking status")
        serial_exists = Path(DEFAULT_PORT).exists()
        setup_exists = Path(DEFAULT_WS, "install", "setup.bash").exists()
        self.status_vars["vehicle"].set("Port found" if serial_exists else "Port missing")
        self.status_vars["ros"].set("Workspace OK" if setup_exists else "Workspace missing")
        self.log(f"Vehicle port {DEFAULT_PORT}: {'OK' if serial_exists else 'MISSING'}")
        self.log(f"ROS workspace {DEFAULT_WS}: {'OK' if setup_exists else 'MISSING'}")
        self.log("RC does not need to be powered on for ROS demos; it remains an emergency override path.")
        self.log("RC failsafe messages with the transmitter off are not a ROS demo blocker.")
        self.run_shell(script_cmd("check_osracer.sh"), name="status-check")

    def start_vehicle_link(self) -> None:
        self.set_action("Starting vehicle link")
        if "vehicle" in self.processes and self.processes["vehicle"].poll() is None:
            self.log("Vehicle link is already running")
            return
        cmd = script_cmd("start_basic_demo.sh")
        self.run_shell(cmd, name="vehicle", keep=True)
        self.status_vars["vehicle"].set("Starting")

    def run_motion(self, demo: str) -> None:
        running_motion = [
            name for name in self.running_process_names()
            if name.startswith(self.motion_prefix)
        ]
        if running_motion:
            messagebox.showinfo("Motion running", "A motion demo is already running. Wait for it to finish or press Emergency Stop.")
            return

        if demo == "warmup":
            label = "Warm-up Motion"
        elif demo == "showcase":
            label = "Line + S Turn"
        elif demo == "figure8":
            label = "Figure-8 Demo"
        elif demo == "circle":
            label = "Continuous Tight Circle"
        else:
            label = demo
        extra = "\n\nThis action keeps running until Emergency Stop is pressed." if demo == "circle" else ""
        if not messagebox.askokcancel("Start motion", f"Confirm the field is safe, then start: {label}{extra}"):
            return
        self.set_action(label)
        cmd = motion_cmd(demo)
        if demo == "figure8":
            cmd += " --loops 1 --duration 14.0 --speed 0.55"
        self.run_shell(cmd, name=f"motion-{demo}")

    def emergency_stop(self) -> None:
        self.set_action("Emergency stop")
        self.status_vars["vehicle"].set("Stopping")
        self.log("Sending emergency stop and cleaning demo ROS processes")
        self.run_shell(script_cmd("stop_all_demo.sh"), name="cleanup")
        self.terminate_processes(names=self.advanced_names | {"vehicle"}, prefix=self.motion_prefix)

    def open_odom_rviz(self) -> None:
        self.start_advanced_once(
            "odom-rviz",
            "Open Odometry RViz",
            script_cmd("open_odom_rviz.sh"),
        )

    def start_mapping(self) -> None:
        self.start_advanced_once(
            "mapping",
            "Mapping",
            script_cmd("start_mapping_demo.sh"),
        )

    def start_navigation(self) -> None:
        self.start_advanced_once(
            "navigation",
            "Navigation",
            script_cmd("start_navigation_demo.sh"),
        )

    def save_map(self) -> None:
        self.set_action("Saving map")
        self.run_shell(f"{script_cmd('save_map_demo.sh')} default", name="save-map")

    def save_cartographer_map(self) -> None:
        self.set_action("Saving cartographer map")
        self.run_shell(f"{script_cmd('save_map_demo.sh')} cartographer", name="save-map")

    def start_active_gmapping(self) -> None:
        self.start_advanced_once(
            "active-gmapping",
            "Driving with GMapping",
            f"{script_cmd('start_active_mapping_demo.sh')} gmapping",
        )

    def start_active_cartographer(self) -> None:
        self.start_advanced_once(
            "active-cartographer",
            "Driving with Cartographer",
            f"{script_cmd('start_active_mapping_demo.sh')} cartographer",
        )

    def start_slam_navigation(self) -> None:
        self.start_advanced_once(
            "slam-navigation",
            "SLAM + Navigation",
            script_cmd("start_slam_navigation_demo.sh"),
        )

    def stop_advanced(self) -> None:
        self.set_action("Stopping advanced nodes")
        self.status_vars["vehicle"].set("Stopping")
        self.log("Stopping advanced nodes and demo ROS background processes")
        self.run_shell(script_cmd("stop_all_demo.sh"), name="cleanup")
        self.terminate_processes(names=self.advanced_names | {"vehicle"})

    def on_close(self) -> None:
        if messagebox.askokcancel("Exit", "Exiting will send stop commands and stop processes launched by this panel."):
            self.close_requested = True
            self.emergency_stop()
            with self.proc_lock:
                procs = [(name, proc) for name, proc in self.processes.items() if name != "cleanup"]
            for _name, proc in procs:
                if proc.poll() is None:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except Exception:
                        pass
            self.after(200, self._finish_close_when_cleanup_done, time.monotonic() + 5.0)

    def _finish_close_when_cleanup_done(self, deadline: float) -> None:
        if not self.process_running("cleanup") or time.monotonic() >= deadline:
            self.destroy()
            return
        self.after(200, self._finish_close_when_cleanup_done, deadline)


def main() -> int:
    app = LeaderDemo()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
