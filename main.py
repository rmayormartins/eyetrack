"""Prancha alfanumérica de CAA controlada pelo olhar."""

from __future__ import annotations

import base64
import threading
import tkinter as tk
from tkinter import font

import cv2
import mediapipe as mp
import numpy as np
import pyautogui


# Ajuste estes limites se o cursor não acompanhar bem o olhar.
CAMERA_INDEX = 0
GAZE_X_RANGE = (0.22, 0.78)
GAZE_Y_RANGE = (0.18, 0.82)
SMOOTHING = 0.10
DWELL_SECONDS = 1.0
CURSOR_MARGIN = 24

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0
mp_face_mesh = mp.solutions.face_mesh


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def gaze_ratio(landmarks, iris_index: int, corner_a: int, corner_b: int,
               upper_lid: int, lower_lid: int) -> tuple[float, float]:
    iris = landmarks[iris_index]
    a, b = landmarks[corner_a], landmarks[corner_b]
    upper, lower = landmarks[upper_lid], landmarks[lower_lid]
    left, right = sorted((a.x, b.x))
    top, bottom = sorted((upper.y, lower.y))
    return ((iris.x - left) / max(right - left, 1e-6),
            (iris.y - top) / max(bottom - top, 1e-6))


class AACBoard:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Eye Track — Prancha de CAA")
        self.root.configure(bg="#f4f7fb")
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.bind("<Escape>", lambda _event: self.close())
        self.root.bind("<F11>", lambda _event: self.toggle_fullscreen())

        self.text = tk.StringVar()
        self.speech_on = tk.BooleanVar(value=True)
        self.page = "letras"
        self.tracking = False
        self.camera = None
        self.face_mesh = None
        self.smoothed = None
        self.look_xy = None
        self.hover_widget = None
        self.hover_started = 0
        self.fired_widget = None
        self.speech_engine = None
        self.speech_lock = threading.Lock()
        self.calibration_targets = ((0.10, 0.10), (0.50, 0.10), (0.90, 0.10),
                                   (0.10, 0.50), (0.50, 0.50), (0.90, 0.50),
                                   (0.10, 0.90), (0.50, 0.90), (0.90, 0.90))
        self.calibration_index = -1
        self.calibration_started = 0
        self.calibration_samples: list[tuple[float, float]] = []
        self.calibration_data: list[tuple[float, float, float, float]] = []
        self.calibration_matrix = None
        self.calibration_marker = None
        self.preview_visible = True
        self.preview_image = None
        self.preview_updated_at = 0
        self.buttons: list[tk.Button] = []

        self.title_font = font.Font(family="Segoe UI", size=20, weight="bold")
        self.key_font = font.Font(family="Segoe UI", size=22, weight="bold")
        self.small_font = font.Font(family="Segoe UI", size=13)
        self.build()
        self.root.lift()
        self.root.focus_force()
        self.root.after(1200, lambda: self.root.attributes("-topmost", False))
        self.root.after(500, self.start_camera_preview)
        self.root.after(30, self.update_tracking)

    def build(self) -> None:
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(2, weight=1)

        header = tk.Frame(self.root, bg="#f4f7fb", padx=18, pady=12)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        tk.Label(header, text="O que você quer dizer?", bg="#f4f7fb",
                 fg="#183153", font=self.title_font).grid(row=0, column=0, sticky="w")
        self.status = tk.Label(header, text="Rastreamento pausado", bg="#f4f7fb",
                               fg="#53657d", font=self.small_font)
        self.status.grid(row=0, column=1, padx=12)
        self.action_button(header, "▶ Iniciar olhar", self.toggle_tracking,
                           color="#2457a7").grid(row=0, column=2, padx=4)
        self.action_button(header, "Calibrar", self.start_calibration,
                           color="#d9f3e7").grid(row=0, column=3, padx=4)
        self.action_button(header, "Câmera", self.toggle_preview,
                           color="#e8f0fc").grid(row=0, column=4, padx=4)
        preview_box = tk.Frame(header, bg="#172b4d", width=200, height=112)
        preview_box.grid(row=0, column=5, padx=(8, 0), rowspan=2)
        preview_box.grid_propagate(False)
        self.preview = tk.Label(preview_box, bg="#172b4d", text="Câmera pausada",
                                fg="white", font=self.small_font)
        self.preview.pack(fill="both", expand=True)

        compose = tk.Frame(self.root, bg="white", padx=12, pady=10,
                           highlightthickness=1, highlightbackground="#d7e0ed")
        compose.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 10))
        compose.grid_columnconfigure(0, weight=1)
        self.output = tk.Label(compose, textvariable=self.text, anchor="w", bg="white",
                               fg="#172b4d", font=("Segoe UI", 23), height=1)
        self.output.grid(row=0, column=0, sticky="ew", padx=6)
        self.action_button(compose, "🔊 Ouvir", self.speak_text, color="#e8f0fc").grid(row=0, column=1, padx=3)
        self.action_button(compose, "⌫", self.backspace, color="#e8f0fc").grid(row=0, column=2, padx=3)
        self.action_button(compose, "Espaço", self.add_space, color="#e8f0fc").grid(row=0, column=3, padx=3)
        self.action_button(compose, "↵ Ler", self.speak_text, color="#e8f0fc").grid(row=0, column=4, padx=3)
        self.action_button(compose, "🗑", self.clear_text, color="#fdeaea").grid(row=0, column=5, padx=3)

        self.keys = tk.Frame(self.root, bg="#f4f7fb", padx=18)
        self.keys.grid(row=2, column=0, sticky="nsew")
        self.keys.grid_columnconfigure(0, weight=1)
        # A faixa de sugestões mantém só a altura necessária; o restante fica
        # reservado para as teclas, inclusive em monitores com pouca altura.
        self.keys.grid_rowconfigure(0, weight=0)
        self.keys.grid_rowconfigure(1, weight=1)
        self.render_keys()

        footer = tk.Frame(self.root, bg="#e8eef7", padx=18, pady=10)
        footer.grid(row=3, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        tk.Label(footer, text="Fixe o olhar em uma tecla por um instante para selecionar",
                 bg="#e8eef7", fg="#43556f", font=self.small_font).grid(row=0, column=0, sticky="w")
        self.action_button(footer, "SIM", lambda: self.say_choice("SIM"), color="#d9f3e7").grid(row=0, column=1, padx=5)
        self.action_button(footer, "NÃO", lambda: self.say_choice("NÃO"), color="#fde1e1").grid(row=0, column=2, padx=5)
        self.action_button(footer, "🔈 Som", self.toggle_speech, color="#e8f0fc").grid(row=0, column=3, padx=5)
        self.action_button(footer, "Sair", self.close, color="#e8eaf0").grid(row=0, column=4, padx=5)

    def action_button(self, parent, label: str, command, color: str = "white") -> tk.Button:
        button = tk.Button(parent, text=label, command=command, bg=color, fg="#183153",
                           activebackground="#c9dcf6", relief="flat", bd=0,
                           padx=14, pady=9, font=("Segoe UI", 14, "bold"),
                           takefocus=False, cursor="hand2")
        self.buttons.append(button)
        return button

    def render_keys(self) -> None:
        for child in self.keys.winfo_children():
            child.destroy()
        self.buttons = [b for b in self.buttons if b.winfo_exists()]
        suggestions = tk.Frame(self.keys, bg="#f4f7fb")
        suggestions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        suggestions.grid_columnconfigure(0, weight=1)
        for i, word in enumerate(("EU", "QUERO", "PRECISO", "ÁGUA", "AJUDA", "OBRIGADO")):
            suggestions.grid_columnconfigure(i, weight=1)
            self.action_button(suggestions, word, lambda w=word: self.add_text(w + " "),
                               color="#e5edf9").grid(row=0, column=i, sticky="ew", padx=3)

        content = tk.Frame(self.keys, bg="#f4f7fb")
        content.grid(row=1, column=0, sticky="nsew")
        self.keys.grid_rowconfigure(1, weight=1)
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(1, weight=1)
        tabs = tk.Frame(content, bg="#f4f7fb")
        tabs.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.action_button(tabs, "ABC · Letras", lambda: self.change_page("letras"),
                           color="#cfe0fa" if self.page == "letras" else "#e8eef7").pack(side="left", padx=(0, 6))
        self.action_button(tabs, "123 · Números", lambda: self.change_page("numeros"),
                           color="#cfe0fa" if self.page == "numeros" else "#e8eef7").pack(side="left")

        grid = tk.Frame(content, bg="#f4f7fb")
        grid.grid(row=1, column=0, sticky="nsew")
        rows = ([list("ABCDEFGHIJ"), list("KLMNOPQRST"), list("UVWXYZ")]
                if self.page == "letras" else
                [list("1234567890"), list("+-*/=()"), list(".,:!?%")])
        for row_index, row in enumerate(rows):
            grid.grid_rowconfigure(row_index, weight=1)
            for col_index, key in enumerate(row):
                grid.grid_columnconfigure(col_index, weight=1)
                self.action_button(grid, key, lambda k=key: self.add_text(k),
                                   color="white").configure(font=self.key_font)
                grid.winfo_children()[-1].grid(row=row_index, column=col_index,
                                                sticky="nsew", padx=4, pady=4)

    def change_page(self, page: str) -> None:
        self.page = page
        self.render_keys()

    def add_text(self, value: str) -> None:
        self.text.set(self.text.get() + value)
        if self.speech_on.get() and len(value.strip()) == 1:
            self.speak(value)

    def add_space(self) -> None:
        self.add_text(" ")

    def backspace(self) -> None:
        self.text.set(self.text.get()[:-1])

    def clear_text(self) -> None:
        self.text.set("")

    def say_choice(self, value: str) -> None:
        self.text.set(value)
        if self.speech_on.get():
            self.speak(value)

    def toggle_speech(self) -> None:
        self.speech_on.set(not self.speech_on.get())
        self.status.config(text="Som ligado" if self.speech_on.get() else "Som desligado")

    def speak_text(self) -> None:
        if self.text.get().strip():
            self.speak(self.text.get())

    def speak(self, text: str) -> None:
        def run() -> None:
            try:
                with self.speech_lock:
                    if self.speech_engine is None:
                        import pyttsx3
                        self.speech_engine = pyttsx3.init()
                        self.speech_engine.setProperty("rate", 155)
                    self.speech_engine.say(text)
                    self.speech_engine.runAndWait()
            except Exception:
                self.root.after(0, lambda: self.status.config(text="Voz indisponível neste computador"))
        threading.Thread(target=run, daemon=True).start()

    def ensure_camera(self) -> bool:
        if self.camera and self.camera.isOpened():
            return True
        camera = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
        if not camera.isOpened():
            camera.release()
            self.status.config(text="Não consegui abrir a webcam")
            return False
        self.camera = camera
        self.face_mesh = mp_face_mesh.FaceMesh(
            max_num_faces=1, refine_landmarks=True,
            min_detection_confidence=0.5, min_tracking_confidence=0.5)
        return True

    def start_camera_preview(self) -> None:
        if self.ensure_camera():
            self.status.config(text="Prévia ativa; rastreamento pausado")

    def toggle_tracking(self) -> None:
        if self.tracking:
            self.tracking = False
            self.calibration_index = -1
            if self.calibration_marker:
                self.calibration_marker.destroy()
                self.calibration_marker = None
            self.status.config(text="Prévia ativa; rastreamento pausado")
            return
        if not self.ensure_camera():
            return
        self.tracking = True
        self.smoothed = None
        self.status.config(text="Olhe para uma tecla e mantenha o olhar")

    def start_calibration(self) -> None:
        if not self.tracking:
            self.toggle_tracking()
        if not self.tracking:
            return
        self.calibration_data = []
        self.calibration_index = 0
        self.show_calibration_target()

    def show_calibration_target(self) -> None:
        if self.calibration_index >= len(self.calibration_targets):
            self.finish_calibration()
            return
        if self.calibration_marker:
            self.calibration_marker.destroy()
        x, y = self.calibration_targets[self.calibration_index]
        self.calibration_marker = tk.Label(
            self.root, text="●", bg="#fff8d8", fg="#d62828",
            font=("Segoe UI", 52, "bold"), padx=8, pady=2,
            highlightthickness=3, highlightbackground="#d62828")
        self.calibration_marker.place(relx=x, rely=y, anchor="center")
        self.calibration_samples = []
        self.calibration_started = self.root.tk.call("clock", "milliseconds")
        self.status.config(text=f"Calibração {self.calibration_index + 1}/9 — olhe para o ponto")
        self.root.after(3000, self.capture_calibration_point)

    def capture_calibration_point(self) -> None:
        if self.calibration_index < 0:
            return
        if len(self.calibration_samples) < 8:
            self.status.config(text="Não detectei seu olhar; mantenha o rosto visível")
            self.root.after(1000, self.capture_calibration_point)
            return
        gx = float(np.median([sample[0] for sample in self.calibration_samples]))
        gy = float(np.median([sample[1] for sample in self.calibration_samples]))
        tx, ty = self.calibration_targets[self.calibration_index]
        self.calibration_data.append((gx, gy, tx, ty))
        self.calibration_index += 1
        self.show_calibration_target()

    def finish_calibration(self) -> None:
        if self.calibration_marker:
            self.calibration_marker.destroy()
            self.calibration_marker = None
        gaze = np.array([[gx, gy, 1.0] for gx, gy, _, _ in self.calibration_data])
        screen = np.array([[tx, ty] for _, _, tx, ty in self.calibration_data])
        self.calibration_matrix, *_ = np.linalg.lstsq(gaze, screen, rcond=None)
        self.calibration_index = -1
        self.smoothed = None
        self.status.config(text="Calibração concluída")

    def update_tracking(self) -> None:
        if self.camera:
            ok, frame = self.camera.read()
            if ok:
                frame = cv2.flip(frame, 1)
                result = self.face_mesh.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                if result.multi_face_landmarks:
                    landmarks = result.multi_face_landmarks[0].landmark
                    self.draw_eye_guides(frame, landmarks)
                    if self.tracking:
                        eyes = (gaze_ratio(landmarks, 468, 33, 133, 159, 145),
                                gaze_ratio(landmarks, 473, 362, 263, 386, 374))
                        target = (sum(p[0] for p in eyes) / 2, sum(p[1] for p in eyes) / 2)
                        if self.calibration_index >= 0:
                            elapsed = self.root.tk.call("clock", "milliseconds") - self.calibration_started
                            if elapsed >= 500:
                                self.calibration_samples.append(target)
                        if self.smoothed is None:
                            self.smoothed = target
                        else:
                            self.smoothed = tuple(old + SMOOTHING * (new - old)
                                                  for old, new in zip(self.smoothed, target))
                        sw, sh = pyautogui.size()
                        if self.calibration_matrix is not None:
                            mapped = np.array([*self.smoothed, 1.0]) @ self.calibration_matrix
                            norm_x, norm_y = mapped
                        else:
                            norm_x = ((self.smoothed[0] - GAZE_X_RANGE[0]) /
                                      (GAZE_X_RANGE[1] - GAZE_X_RANGE[0]))
                            norm_y = ((self.smoothed[1] - GAZE_Y_RANGE[0]) /
                                      (GAZE_Y_RANGE[1] - GAZE_Y_RANGE[0]))
                        # Mantém o cursor longe do canto de fail-safe do PyAutoGUI.
                        x = clamp(float(norm_x) * (sw - 1), CURSOR_MARGIN, sw - 1 - CURSOR_MARGIN)
                        y = clamp(float(norm_y) * (sh - 1), CURSOR_MARGIN, sh - 1 - CURSOR_MARGIN)
                        self.look_xy = (int(x), int(y))
                        try:
                            pyautogui.moveTo(*self.look_xy, duration=0)
                        except pyautogui.FailSafeException:
                            self.stop_tracking()
                            self.status.config(text="Pausado pelo fail-safe")
                        if self.calibration_index < 0:
                            self.handle_dwell()
                    else:
                        self.status.config(text="Prévia ativa; rastreamento pausado")
                else:
                    cv2.putText(frame, "Rosto nao detectado", (12, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 180, 255), 2)
                    self.hover_widget = None
                    self.fired_widget = None
                    self.status.config(text="Rosto não detectado")
                if self.preview_visible:
                    self.update_preview(frame)
            else:
                self.preview.configure(image="", text="Sem sinal da webcam")
                self.status.config(text="Sem imagem da webcam")
        self.root.after(30, self.update_tracking)

    def draw_eye_guides(self, frame, landmarks) -> None:
        height, width = frame.shape[:2]
        for iris, corners, lids in (
            (468, (33, 133), (159, 145)),
            (473, (362, 263), (386, 374)),
        ):
            def xy(index):
                point = landmarks[index]
                return int(point.x * width), int(point.y * height)

            left, right = (xy(index) for index in corners)
            upper, lower = (xy(index) for index in lids)
            cv2.rectangle(frame, left, right, (255, 190, 0), 1)
            cv2.line(frame, upper, lower, (255, 190, 0), 1)
            cv2.circle(frame, xy(iris), 5, (0, 255, 0), -1)
            cv2.circle(frame, xy(iris), 9, (255, 255, 255), 1)

    def update_preview(self, frame) -> None:
        now = self.root.tk.call("clock", "milliseconds")
        if now - self.preview_updated_at < 100:
            return
        self.preview_updated_at = now
        try:
            small = cv2.resize(frame, (200, 112))
            ok, png = cv2.imencode(".png", small)
            if not ok:
                raise RuntimeError("OpenCV não conseguiu converter o quadro para PNG")
            encoded = base64.b64encode(png.tobytes()).decode("ascii")
            self.preview_image = tk.PhotoImage(data=encoded, format="PNG")
            self.preview.configure(image=self.preview_image, text="")
        except Exception as exc:
            self.preview.configure(image="", text="Prévia indisponível")
            self.status.config(text=f"Falha na prévia: {exc}")

    def toggle_preview(self) -> None:
        self.preview_visible = not self.preview_visible
        if self.preview_visible:
            self.preview.grid()
        else:
            self.preview.grid_remove()

    def handle_dwell(self) -> None:
        if not self.look_xy:
            return
        x, y = self.look_xy
        try:
            widget = self.root.winfo_containing(x, y)
        except tk.TclError:
            widget = None
        while widget is not None and not isinstance(widget, tk.Button):
            widget = getattr(widget, "master", None)
        now = self.root.tk.call("clock", "milliseconds")
        if widget is not self.hover_widget:
            self.hover_widget, self.hover_started, self.fired_widget = widget, now, None
        if widget is None:
            return
        if now - self.hover_started >= DWELL_SECONDS * 1000 and self.fired_widget is not widget:
            self.fired_widget = widget
            widget.invoke()

    def stop_tracking(self) -> None:
        self.tracking = False
        if self.face_mesh:
            self.face_mesh.close()
            self.face_mesh = None
        if self.camera:
            self.camera.release()
            self.camera = None
        if hasattr(self, "preview"):
            self.preview_image = None
            self.preview.configure(image="", text="Câmera pausada")

    def toggle_fullscreen(self) -> None:
        self.root.attributes("-fullscreen", not self.root.attributes("-fullscreen"))

    def close(self) -> None:
        self.stop_tracking()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    AACBoard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
