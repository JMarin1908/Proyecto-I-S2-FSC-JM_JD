"""
Interfaz gráfica para controlar el vehículo (Raspberry Pi Pico W) por WiFi.
La PC actúa como servidor TCP (como en server.py) y la Pico se conecta como cliente.

Ejecutar:  python interfaz_vehiculo.py
Requiere:  Python 3 con tkinter (Pillow opcional para fotos JPG)

TECLADO
  Flechas ↑ ↓      avanzar / retroceder (mientras se mantienen presionadas)
  Flechas ← →      girar (al soltar, la dirección vuelve al centro)
  1 - 4            nivel de velocidad
  F / B            luces frontales / traseras
  Q / E            direccional izquierda / derecha
  Espacio / Esc    STOP
"""
import socket
import threading
import queue
import tkinter as tk
from tkinter import ttk

# =====================================================
#  CONFIGURACIÓN
# =====================================================
HOST = "0.0.0.0" #Escuchar en todas las interfaces de red
PORT = 8001 #Configurar a conveniencia (debe coincidir con la Pico)

NOMBRE_PROGRAMA = "Control de Vehículo WiFi"
VERSION = "1.1"
INSTITUCION = "Nombre de la institución"          # <-- completar
INTEGRANTES = [                                    # <-- completar
    {"nombre": "Integrante 1", "foto": "foto1.png"},
    {"nombre": "Integrante 2", "foto": "foto2.png"},
    {"nombre": "Integrante 3", "foto": "foto3.png"},
]

# ---------- Paleta (cámbiala aquí para ajustar el estilo) ----------
C = {
    "bg":      "#0d1117",
    "panel":   "#161b26",
    "panel2":  "#1e2433",
    "borde":   "#2b3350",
    "texto":   "#e6edf3",
    "tenue":   "#8b949e",
    "acento":  "#00e5ff",
    "acento2": "#ff2e88",
    "ok":      "#3fb950",
    "error":   "#f85149",
    "ambar":   "#ffb300",
    "peligro": "#c62828",
}
FUENTE = ("Segoe UI", 10)
FUENTE_B = ("Segoe UI", 10, "bold")
FUENTE_T = ("Segoe UI", 18, "bold")

MOVIMIENTO = ("Up", "Down", "Left", "Right")


# =====================================================
#  SERVIDOR TCP (hilo aparte)
# =====================================================
class Servidor(threading.Thread):
    def __init__(self, cola):
        super().__init__(daemon=True)
        self.cola = cola
        self.conn = None
        self.lock = threading.Lock()

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((HOST, PORT))
            srv.listen(1)
        except OSError as e:
            self.cola.put(("estado", "Error al iniciar servidor: %s" % e))
            return
        self.cola.put(("estado", "Esperando a la Pico en el puerto %d..." % PORT))

        while True:
            conn, addr = srv.accept()
            with self.lock:
                self.conn = conn
            self.cola.put(("conectado", "Conectado: %s:%d" % addr))
            self.atender(conn)
            with self.lock:
                self.conn = None
            self.cola.put(("estado", "Pico desconectada. Esperando..."))

    def atender(self, conn):
        buf = b""
        try:
            while True:
                data = conn.recv(1024)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    linea, buf = buf.split(b"\n", 1)
                    self.cola.put(("rx", linea.decode(errors="ignore").strip()))
        except OSError:
            pass
        finally:
            conn.close()

    def enviar(self, comando):
        with self.lock:
            if self.conn is None:
                return False
            try:
                self.conn.sendall((comando + "\n").encode())
                return True
            except OSError:
                return False


def bits(n):
    return format(n, "03b")


def esperado(n):
    """Decrementador circular de 5: resta 5 en módulo 8 (7 -> 2, 5 -> 0, 0 -> 3)."""
    return (n - 5) % 8


# =====================================================
#  INTERFAZ
# =====================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("%s v%s" % (NOMBRE_PROGRAMA, VERSION))
        self.configure(bg=C["bg"])
        self.resizable(False, False)

        self.cola = queue.Queue()
        self.servidor = Servidor(self.cola)
        self.servidor.start()

        # Estado de teclado
        self.held = set()
        self.release_jobs = {}
        self.otras_presionadas = set()
        self.last_pwm = 0
        self.last_dir = 0

        # Estado local (para dibujar el auto)
        self.blink = False
        self.dec_estado = None          # None o (entrada, salida, ldr)
        self.fotos = []

        self.configurar_estilos()
        self.construir_menu()
        self.construir_ui()
        self.configurar_teclado()
        self.dibujar_auto()
        self.after(100, self.revisar_cola)
        self.after(500, self.parpadear)

    # ---------- Estilos ----------
    def configurar_estilos(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame", background=C["bg"])
        s.configure("Panel.TLabelframe", background=C["panel"], bordercolor=C["borde"],
                    relief="solid", borderwidth=1)
        s.configure("Panel.TLabelframe.Label", background=C["panel"],
                    foreground=C["acento"], font=FUENTE_B)
        s.configure("TLabel", background=C["panel"], foreground=C["texto"], font=FUENTE)
        s.configure("Tenue.TLabel", foreground=C["tenue"])
        s.configure("TCheckbutton", background=C["panel"], foreground=C["texto"],
                    font=FUENTE, focuscolor=C["panel"])
        s.map("TCheckbutton", background=[("active", C["panel2"])],
              indicatorcolor=[("selected", C["acento"]), ("!selected", C["bg"])])
        s.configure("TButton", background=C["panel2"], foreground=C["texto"],
                    font=FUENTE_B, bordercolor=C["borde"], padding=6, focuscolor=C["panel2"])
        s.map("TButton", background=[("active", C["borde"])])

    def boton(self, parent, texto, comando, color=None, ancho=None):
        color = color or C["panel2"]
        b = tk.Button(parent, text=texto, command=comando, bg=color, fg=C["texto"],
                      activebackground=C["acento"], activeforeground="#000",
                      relief="flat", bd=0, font=FUENTE_B, padx=10, pady=6,
                      cursor="hand2", width=ancho, takefocus=0)
        return b

    def panel(self, parent, titulo):
        return ttk.LabelFrame(parent, text=" %s " % titulo, style="Panel.TLabelframe")

    # ---------- Menú ----------
    def construir_menu(self):
        menu = tk.Menu(self)
        ayuda = tk.Menu(menu, tearoff=0)
        ayuda.add_command(label="Acerca de...", command=self.mostrar_about)
        menu.add_cascade(label="About", menu=ayuda)
        self.config(menu=menu)

    # ---------- UI ----------
    def construir_ui(self):
        # Encabezado
        enc = tk.Frame(self, bg=C["bg"])
        enc.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 4))
        tk.Label(enc, text="◢ ", bg=C["bg"], fg=C["acento2"], font=FUENTE_T).pack(side="left")
        tk.Label(enc, text=NOMBRE_PROGRAMA.upper(), bg=C["bg"], fg=C["acento"],
                 font=FUENTE_T).pack(side="left")
        self.lbl_estado = tk.Label(enc, text="● Iniciando servidor...", bg=C["panel"],
                                   fg=C["ambar"], font=FUENTE_B, padx=12, pady=4)
        self.lbl_estado.pack(side="right")

        # ---------------- Columna izquierda: auto ----------------
        izq = tk.Frame(self, bg=C["bg"])
        izq.grid(row=1, column=0, sticky="n", padx=(12, 6), pady=4)

        f_auto = self.panel(izq, "Vista del vehículo")
        f_auto.pack(fill="x")
        self.canvas = tk.Canvas(f_auto, width=250, height=330, bg=C["panel"],
                                highlightthickness=0)
        self.canvas.pack(padx=8, pady=8)

        f_teclas = self.panel(izq, "Teclado")
        f_teclas.pack(fill="x", pady=(8, 0))
        ayuda = ("↑ ↓   avanzar / retroceder\n"
                 "← →   girar\n"
                 "1-4   velocidad\n"
                 "F B   frontales / traseras\n"
                 "Q E   direccional izq. / der.\n"
                 "Espacio · Esc   STOP")
        ttk.Label(f_teclas, text=ayuda, justify="left").pack(padx=10, pady=8, anchor="w")

        # ---------------- Columna derecha: controles ----------------
        der = tk.Frame(self, bg=C["bg"])
        der.grid(row=1, column=1, sticky="n", padx=(6, 12), pady=4)

        # Velocidad / potencia
        f_pwm = self.panel(der, "Potencia (pwm)  ·  negativo = retroceso")
        f_pwm.pack(fill="x")
        self.var_pwm = tk.IntVar(value=0)
        fila = tk.Frame(f_pwm, bg=C["panel"])
        fila.pack(padx=8, pady=8)
        for v in range(-4, 5):
            tk.Radiobutton(fila, text=str(v), value=v, variable=self.var_pwm,
                           indicatoron=0, width=4, pady=6, bg=C["panel2"], fg=C["texto"],
                           selectcolor=C["acento2"], activebackground=C["borde"],
                           relief="flat", bd=0, font=FUENTE_B, takefocus=0,
                           command=self.pwm_manual).pack(side="left", padx=2)

        f_vel = tk.Frame(f_pwm, bg=C["panel"])
        f_vel.pack(padx=8, pady=(0, 8), anchor="w")
        ttk.Label(f_vel, text="Velocidad del teclado:").pack(side="left")
        self.var_nivel = tk.IntVar(value=2)
        for n in range(1, 5):
            tk.Radiobutton(f_vel, text=str(n), value=n, variable=self.var_nivel,
                           indicatoron=0, width=3, bg=C["panel2"], fg=C["texto"],
                           selectcolor=C["acento"], activeforeground="#000",
                           relief="flat", bd=0, font=FUENTE_B, takefocus=0,
                           command=self.cambio_nivel).pack(side="left", padx=2)

        # Dirección
        f_dir = self.panel(der, "Dirección")
        f_dir.pack(fill="x", pady=(8, 0))
        fd = tk.Frame(f_dir, bg=C["panel"])
        fd.pack(padx=8, pady=8)
        self.boton(fd, "◄ Izquierda", lambda: self.enviar("dir -1")).pack(side="left", padx=3)
        self.boton(fd, "▲ Recto", lambda: self.enviar("dir 0")).pack(side="left", padx=3)
        self.boton(fd, "Derecha ►", lambda: self.enviar("dir 1")).pack(side="left", padx=3)
        self.boton(fd, "▼ Retroceder", lambda: self.enviar("dir 2")).pack(side="left", padx=3)

        # Luces
        f_luces = self.panel(der, "Luces")
        f_luces.pack(fill="x", pady=(8, 0))
        self.vars_luces = {}
        etiquetas = [("lf", "Frontales (F)"), ("lb", "Traseras (B)"),
                     ("ll", "Direc. izq. (Q)"), ("lr", "Direc. der. (E)"),
                     ("ldr", "Automático LDR")]
        for i, (cmd, texto) in enumerate(etiquetas):
            v = tk.IntVar(value=0)
            self.vars_luces[cmd] = v
            ttk.Checkbutton(f_luces, text=texto, variable=v, takefocus=0,
                            command=lambda c=cmd: self.enviar_luz(c)
                            ).grid(row=i // 3, column=i % 3, sticky="w", padx=10, pady=5)

        # Patrones
        f_pat = self.panel(der, "Patrones")
        f_pat.pack(fill="x", pady=(8, 0))
        fp = tk.Frame(f_pat, bg=C["panel"])
        fp.pack(padx=8, pady=8)
        for cmd, texto in [("circle", "Circle"), ("infinite", "Infinite"),
                           ("zigzag", "ZigZag"), ("especial", "Especial")]:
            self.boton(fp, texto, lambda c=cmd: self.enviar(c), ancho=9).pack(side="left", padx=3)

        # STOP
        tk.Button(der, text="■  STOP / APAGAR TODO", bg=C["peligro"], fg="white",
                  activebackground="#e53935", activeforeground="white", relief="flat",
                  font=("Segoe UI", 12, "bold"), pady=8, cursor="hand2", takefocus=0,
                  command=self.stop_total).pack(fill="x", pady=(8, 0))

        # ---------------- Decrementador (ancho completo) ----------------
        f_dec = self.panel(self, "Decrementador circular de 5  (resta 5 módulo 8)")
        f_dec.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(8, 4))

        self.var_dec_ldr = tk.IntVar(value=0)
        ttk.Checkbutton(f_dec, text="Activar decrementador con el LDR (8 intervalos → 3 bits)",
                        variable=self.var_dec_ldr, takefocus=0, command=self.toggle_dec_ldr
                        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 4))

        f_man = tk.Frame(f_dec, bg=C["panel"])
        f_man.grid(row=1, column=0, sticky="n", padx=10, pady=4)
        ttk.Label(f_man, text="Pruebas manuales", foreground=C["acento2"],
                  font=FUENTE_B).grid(row=0, column=0, columnspan=4, sticky="w")
        self.var_bits = [tk.IntVar(value=0) for _ in range(3)]
        for i, nombre in enumerate("ABC"):
            ttk.Checkbutton(f_man, text=nombre, variable=self.var_bits[i], takefocus=0,
                            command=self.enviar_manual).grid(row=1, column=i, padx=6, pady=6)
        self.boton(f_man, "Desactivar", self.desactivar_manual).grid(row=1, column=3, padx=6)

        f_res = tk.Frame(f_dec, bg=C["panel"])
        f_res.grid(row=1, column=1, sticky="n", padx=10, pady=4)
        self.lbl_in = ttk.Label(f_res, text="Entrada  A B C :  ---", font=("Consolas", 11))
        self.lbl_in.grid(row=0, column=0, sticky="w")
        self.lbl_out = ttk.Label(f_res, text="Salida   X Y Z :  ---", font=("Consolas", 11))
        self.lbl_out.grid(row=1, column=0, sticky="w")
        self.lbl_esp = ttk.Label(f_res, text="Esperado       :  ---", font=("Consolas", 11))
        self.lbl_esp.grid(row=2, column=0, sticky="w")
        self.lbl_ldr = ttk.Label(f_res, text="Intervalo LDR  :  ---", font=("Consolas", 11))
        self.lbl_ldr.grid(row=3, column=0, sticky="w")
        self.lbl_veredicto = tk.Label(f_res, text="", bg=C["panel"], font=FUENTE_B)
        self.lbl_veredicto.grid(row=4, column=0, sticky="w", pady=(4, 0))

        # LED virtuales
        self.cv_leds = tk.Canvas(f_dec, width=170, height=70, bg=C["panel"],
                                 highlightthickness=0)
        self.cv_leds.grid(row=1, column=2, padx=10, pady=4)
        self.leds = []
        nombres = ["X · frontales", "Y · direc.", "Z · traseras"]
        for i in range(3):
            x = 28 + i * 57
            self.leds.append(self.cv_leds.create_oval(x - 16, 8, x + 16, 40,
                                                      fill=C["borde"], outline=C["tenue"]))
            self.cv_leds.create_text(x, 56, text=nombres[i], fill=C["tenue"],
                                     font=("Segoe UI", 8))

        # Log
        f_log = self.panel(self, "Mensajes")
        f_log.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(4, 12))
        self.log = tk.Text(f_log, height=6, width=84, state="disabled", bg=C["bg"],
                           fg=C["ok"], font=("Consolas", 9), relief="flat", takefocus=0)
        self.log.pack(padx=8, pady=8)

    # ---------- Teclado ----------
    def configurar_teclado(self):
        self.bind_all("<KeyPress>", self.tecla_presionada)
        self.bind_all("<KeyRelease>", self.tecla_soltada)
        # evita que un botón con foco reaccione a la barra espaciadora
        self.bind_all("<ButtonRelease-1>", lambda e: self.after_idle(self.focus_set))
        self.focus_set()

    def tecla_presionada(self, e):
        k = e.keysym
        if k in MOVIMIENTO:
            job = self.release_jobs.pop(k, None)
            if job:
                self.after_cancel(job)           # era auto-repetición, no soltó
            if k not in self.held:
                self.held.add(k)
                self.actualizar_movimiento()
            return "break"

        k = k.lower()
        if k in self.otras_presionadas:
            return "break"
        self.otras_presionadas.add(k)

        if k in ("1", "2", "3", "4"):
            self.var_nivel.set(int(k))
            self.cambio_nivel()
        elif k == "f":
            self.alternar_luz("lf")
        elif k == "b":
            self.alternar_luz("lb")
        elif k == "q":
            self.alternar_luz("ll")
        elif k == "e":
            self.alternar_luz("lr")
        elif k in ("space", "escape"):
            self.stop_total()
        return "break"

    def tecla_soltada(self, e):
        k = e.keysym
        if k in MOVIMIENTO:
            # Se espera un instante: en algunos sistemas la auto-repetición
            # genera un "soltar" falso justo antes de cada "presionar".
            if k not in self.release_jobs:
                self.release_jobs[k] = self.after(40, lambda kk=k: self.soltar_definitivo(kk))
        else:
            self.otras_presionadas.discard(k.lower())
        return "break"

    def soltar_definitivo(self, k):
        self.release_jobs.pop(k, None)
        self.held.discard(k)
        self.actualizar_movimiento()

    def actualizar_movimiento(self):
        nivel = self.var_nivel.get()
        arriba, abajo = "Up" in self.held, "Down" in self.held
        if arriba and not abajo:
            pwm = nivel
        elif abajo and not arriba:
            pwm = -nivel
        else:
            pwm = 0
        giro = ("Right" in self.held) - ("Left" in self.held)

        if pwm != self.last_pwm:
            self.last_pwm = pwm
            self.var_pwm.set(pwm)
            self.enviar("pwm %d" % pwm)
        if giro != self.last_dir:
            self.last_dir = giro
            self.enviar("dir %d" % giro)
        self.dibujar_auto()

    def cambio_nivel(self):
        if self.last_pwm != 0:
            self.last_pwm = 0        # fuerza el reenvío con el nuevo nivel
            self.actualizar_movimiento()

    def pwm_manual(self):
        self.last_pwm = self.var_pwm.get()
        self.enviar("pwm %d" % self.last_pwm)
        self.dibujar_auto()

    def alternar_luz(self, cmd):
        v = self.vars_luces[cmd]
        v.set(0 if v.get() else 1)
        self.enviar_luz(cmd)

    def enviar_luz(self, cmd):
        self.enviar("%s %d" % (cmd, self.vars_luces[cmd].get()))
        self.dibujar_auto()

    # ---------- Envío ----------
    def enviar(self, comando):
        if self.servidor.enviar(comando):
            self.escribir_log("> " + comando)
        else:
            self.escribir_log("(!) Sin conexión con la Pico: " + comando)

    def enviar_manual(self):
        a, b, c = (v.get() for v in self.var_bits)
        self.var_dec_ldr.set(0)
        self.enviar("dec %d" % (a * 4 + b * 2 + c))

    def desactivar_manual(self):
        for v in self.var_bits:
            v.set(0)
        self.enviar("decoff")

    def toggle_dec_ldr(self):
        self.enviar("decldr %d" % self.var_dec_ldr.get())

    def stop_total(self):
        self.enviar("stop")
        for job in self.release_jobs.values():
            self.after_cancel(job)
        self.release_jobs.clear()
        self.held.clear()
        self.last_pwm = 0
        self.last_dir = 0
        self.var_pwm.set(0)
        for v in self.vars_luces.values():
            v.set(0)
        for v in self.var_bits:
            v.set(0)
        self.var_dec_ldr.set(0)
        self.dec_estado = None
        self.refrescar_decrementador()
        self.dibujar_auto()

    # ---------- Recepción ----------
    def revisar_cola(self):
        while not self.cola.empty():
            tipo, texto = self.cola.get()
            if tipo == "conectado":
                self.lbl_estado.config(text="● " + texto, fg=C["ok"])
                self.escribir_log(texto)
            elif tipo == "estado":
                self.lbl_estado.config(text="● " + texto, fg=C["ambar"])
                self.escribir_log(texto)
            else:
                self.procesar_mensaje(texto)
        self.after(100, self.revisar_cola)

    def procesar_mensaje(self, texto):
        partes = texto.split()
        if partes and partes[0] == "DEC" and len(partes) == 4:
            self.dec_estado = (int(partes[1]), int(partes[2]), int(partes[3]))
            self.refrescar_decrementador()
            self.dibujar_auto()
        elif partes and partes[0] == "DECOFF":
            self.dec_estado = None
            self.refrescar_decrementador()
            self.dibujar_auto()
        else:
            self.escribir_log("< " + texto)

    def refrescar_decrementador(self):
        if self.dec_estado is None:
            self.lbl_in.config(text="Entrada  A B C :  ---")
            self.lbl_out.config(text="Salida   X Y Z :  ---")
            self.lbl_esp.config(text="Esperado       :  ---")
            self.lbl_ldr.config(text="Intervalo LDR  :  ---")
            self.lbl_veredicto.config(text="")
            for led in self.leds:
                self.cv_leds.itemconfig(led, fill=C["borde"])
            return
        e, s, l = self.dec_estado
        esp = esperado(e)
        self.lbl_in.config(text="Entrada  A B C :  %s  (%d)" % (bits(e), e))
        self.lbl_out.config(text="Salida   X Y Z :  %s  (%d)" % (bits(s), s))
        self.lbl_esp.config(text="Esperado       :  %s  (%d)" % (bits(esp), esp))
        self.lbl_ldr.config(text=("Intervalo LDR  :  %d" % l) if self.var_dec_ldr.get()
                            else "Intervalo LDR  :  ---")
        if s == esp:
            self.lbl_veredicto.config(text="✔ La salida coincide con la tabla", fg=C["ok"])
        else:
            self.lbl_veredicto.config(text="✘ La salida NO coincide con la tabla", fg=C["error"])
        colores = [C["ambar"], C["acento"], C["error"]]
        for i in range(3):
            on = (s >> (2 - i)) & 1
            self.cv_leds.itemconfig(self.leds[i], fill=colores[i] if on else C["borde"])

    def escribir_log(self, texto):
        self.log.config(state="normal")
        self.log.insert("end", texto + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    # ---------- Dibujo del auto ----------
    def parpadear(self):
        self.blink = not self.blink
        self.dibujar_auto()
        self.after(500, self.parpadear)

    def dibujar_auto(self):
        cv = self.canvas
        cv.delete("all")

        # Estado de luces (aproximación local de lo que hace la Pico)
        if self.dec_estado is not None:
            s = self.dec_estado[1]
            front, rear = bool(s & 4), bool(s & 1)
            izq = der = bool(s & 2)
        else:
            front = bool(self.vars_luces["lf"].get())
            rear = bool(self.vars_luces["lb"].get()) or self.last_pwm < 0
            izq = bool(self.vars_luces["ll"].get()) and self.blink
            der = bool(self.vars_luces["lr"].get()) and self.blink
        giro = self.last_dir

        # Haces de luz
        if front:
            cv.create_polygon(80, 52, 100, 52, 112, 6, 60, 6, fill="#fff59d", stipple="gray25",
                              outline="")
            cv.create_polygon(150, 52, 170, 52, 190, 6, 138, 6, fill="#fff59d", stipple="gray25",
                              outline="")

        # Carrocería
        cv.create_polygon(75, 70, 175, 70, 182, 100, 182, 270, 168, 292, 82, 292, 68, 270,
                          68, 100, smooth=True, fill=C["panel2"], outline=C["acento"], width=2)
        # Cabina
        cv.create_polygon(88, 120, 162, 120, 158, 215, 92, 215, smooth=True,
                          fill=C["bg"], outline=C["acento2"], width=2)
        cv.create_line(100, 150, 150, 150, fill=C["borde"])
        # Ruedas traseras
        cv.create_rectangle(52, 230, 68, 275, fill="#222", outline=C["tenue"])
        cv.create_rectangle(182, 230, 198, 275, fill="#222", outline=C["tenue"])
        # Ruedas delanteras (inclinadas según el giro)
        d = giro * 9
        for x0 in (52, 182):
            cv.create_polygon(x0 + d, 78, x0 + 16 + d, 78, x0 + 16 - d, 123, x0 - d, 123,
                              fill="#222", outline=C["acento"])

        # Luces frontales
        col = "#fff59d" if front else "#3a3f4f"
        cv.create_oval(80, 56, 102, 72, fill=col, outline=C["tenue"])
        cv.create_oval(148, 56, 170, 72, fill=col, outline=C["tenue"])
        # Luces traseras
        col = "#ff1744" if rear else "#4a2328"
        cv.create_oval(80, 278, 102, 291, fill=col, outline=C["tenue"])
        cv.create_oval(148, 278, 170, 291, fill=col, outline=C["tenue"])
        # Direccionales
        col_i = C["ambar"] if izq else "#3d3520"
        col_d = C["ambar"] if der else "#3d3520"
        cv.create_oval(60, 84, 74, 98, fill=col_i, outline="")
        cv.create_oval(60, 262, 74, 276, fill=col_i, outline="")
        cv.create_oval(176, 84, 190, 98, fill=col_d, outline="")
        cv.create_oval(176, 262, 190, 276, fill=col_d, outline="")

        # Indicador de potencia
        cv.create_text(125, 315, text="pwm %+d" % self.last_pwm if self.last_pwm else "pwm 0",
                       fill=C["acento"], font=("Consolas", 10, "bold"))

    # ---------- About ----------
    def mostrar_about(self):
        win = tk.Toplevel(self)
        win.title("Acerca de")
        win.configure(bg=C["panel"])
        win.resizable(False, False)

        tk.Label(win, text=NOMBRE_PROGRAMA, bg=C["panel"], fg=C["acento"],
                 font=("Segoe UI", 15, "bold")).pack(pady=(12, 0))
        tk.Label(win, text="Versión %s" % VERSION, bg=C["panel"], fg=C["tenue"],
                 font=FUENTE).pack()
        tk.Label(win, text=INSTITUCION, bg=C["panel"], fg=C["acento2"],
                 font=("Segoe UI", 11, "italic")).pack(pady=(4, 8))

        f_int = tk.Frame(win, bg=C["panel"])
        f_int.pack(padx=10)
        for i, p in enumerate(INTEGRANTES):
            col = tk.Frame(f_int, bg=C["panel"])
            col.grid(row=0, column=i, padx=8)
            foto = self.cargar_foto(p["foto"])
            if foto:
                tk.Label(col, image=foto, bg=C["panel"]).pack()
            else:
                tk.Label(col, text="[sin foto]", width=16, height=7, bg=C["panel2"],
                         fg=C["tenue"]).pack()
            tk.Label(col, text=p["nombre"], bg=C["panel"], fg=C["texto"], font=FUENTE_B,
                     wraplength=140, justify="center").pack(pady=(4, 0))

        guia = ("Guía básica\n"
                "1. Ejecute este programa en la PC y espere a que aparezca “Esperando a la Pico”.\n"
                "2. Encienda la Pico W: se conectará sola a la red y a la PC.\n"
                "3. Maneje con las flechas del teclado o con los botones.\n"
                "4. Decrementador: use las pruebas manuales (A B C) o active el LDR.\n"
                "    La salida (X Y Z) se ve en los LED del vehículo:\n"
                "    X = frontales (MSB), Y = direccionales, Z = traseras (LSB).\n"
                "5. STOP (Espacio / Esc) detiene el vehículo y apaga las luces.")
        tk.Label(win, text=guia, bg=C["panel"], fg=C["texto"], font=FUENTE,
                 justify="left").pack(padx=14, pady=12)
        self.boton(win, "Cerrar", win.destroy).pack(pady=(0, 12))

    def cargar_foto(self, ruta):
        try:
            try:
                from PIL import Image, ImageTk
                img = Image.open(ruta)
                img.thumbnail((140, 140))
                foto = ImageTk.PhotoImage(img)
            except ImportError:
                foto = tk.PhotoImage(file=ruta)
            self.fotos.append(foto)
            return foto
        except Exception:
            return None


if __name__ == "__main__":
    App().mainloop()
