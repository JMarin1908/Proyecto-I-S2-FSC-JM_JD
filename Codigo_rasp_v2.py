import utime
import network
import socket
from machine import Pin, PWM, ADC, Timer

# =====================================================
#  CONFIGURACIÓN DE RED
# =====================================================
SSID = "********" # Configurar
PASSWORD = "********" # Configurar
SERVER_ADDRESS = ('000.000.000.000', 0000)   # IP de la PC donde corre interfaz_vehiculo.py

# =====================================================
#  PINES
# =====================================================

# ---------- Motor 1: tracción (adelante / atrás) ----------
ena = PWM(Pin(16))
ena.freq(1000)
ena.duty_u16(0)
in1 = Pin(17, Pin.OUT)
in2 = Pin(18, Pin.OUT)

# ---------- Motor 2: dirección (derecha / izquierda) ----------
enb = PWM(Pin(19))
enb.freq(1000)
enb.duty_u16(0)
in3 = Pin(20, Pin.OUT)
in4 = Pin(21, Pin.OUT)

# ---------- SN74LS164 (registro de desplazamiento) ----------
DATA = Pin(15, Pin.OUT)     # entradas A y B unidas
CLK = Pin(14, Pin.OUT)      # CLK

# ---------- Fotorresistencia (LDR) ----------
ldr = ADC(26)               # GP26 = ADC0

# ---------- Circuito decrementador ----------
# Entradas del circuito (la Pico ESCRIBE): A = MSB, C = LSB
PIN_A = Pin(11, Pin.OUT)
PIN_B = Pin(12, Pin.OUT)
PIN_C = Pin(13, Pin.OUT)
# Salidas del circuito (la Pico LEE): X = MSB, Z = LSB
PIN_X = Pin(7, Pin.IN)
PIN_Y = Pin(8, Pin.IN)
PIN_Z = Pin(9, Pin.IN)

# =====================================================
#  CONSTANTES
# =====================================================
NIVELES = {1: 32000, 2: 43000, 3: 54000, 4: 65535}

T_DIR = 0.4
DUTY_GIRO = 65535
DUTY_MANTENER = 55000
T_FRENO = 1000
UMBRAL_LDR = 20000

T_CIRCULO = 4
T_ZIGZAG = 1
REP_ZIGZAG = 4

# Bits de salida del 74LS164 (QA = bit 0 ... QH = bit 7)
LUZ_FRENO = (1 << 0) | (1 << 1)          # QA y QB  (traseras)
LUZ_FRONTALES = (1 << 2) | (1 << 3)      # QC y QD  (frontales)
DIR_IZQ = 1 << 4                         # QE
DIR_DER = 1 << 5                         # QF

# Visualización del decrementador:
#   X (MSB)        -> luces frontales
#   Y (intermedio) -> direccionales (izq + der)
#   Z (LSB)        -> luces traseras

# =====================================================
#  VARIABLES DE ESTADO
# =====================================================
potencia = 0
dir_actual = 0
lf = 0
lb = 0
ll = 0
lr = 0
ldr_auto = 0
oscuro = False
parpadeo = False
freno_hasta = 0
ocupado = False

# Decrementador
dec_manual = False   # modo pruebas manuales (valor enviado desde la PC)
dec_ldr = False      # modo automático con la fotorresistencia
dec_in = 0           # valor enviado a A,B,C
dec_out = 0          # valor leído de X,Y,Z
dec_ldr_val = 0      # intervalo (0-7) detectado por el LDR


# =====================================================
#  DECREMENTADOR
# =====================================================
def escribir_entrada(n):
    """Pone el valor n (0-7) en los pines A (MSB), B y C (LSB)."""
    PIN_A.value((n >> 2) & 1)
    PIN_B.value((n >> 1) & 1)
    PIN_C.value(n & 1)


def leer_salida():
    """Lee X (MSB), Y, Z (LSB) y devuelve el valor 0-7."""
    return (PIN_X.value() << 2) | (PIN_Y.value() << 1) | PIN_Z.value()


def actualizar_decrementador():
    """Envía la entrada al circuito y lee su salida."""
    global dec_in, dec_out, dec_ldr_val
    if dec_ldr:
        # Se divide el rango del LDR (0-65535) en 8 intervalos (0-7)
        dec_ldr_val = min(7, (ldr.read_u16() * 8) >> 16)
        dec_in = dec_ldr_val
    elif not dec_manual:
        return
    escribir_entrada(dec_in)
    utime.sleep_ms(2)            # tiempo para que se propague la señal
    dec_out = leer_salida()


# =====================================================
#  LUCES (74LS164)
# =====================================================
def actualizar_luces():
    """Arma el byte de luces y lo envía al 74LS164."""
    global ocupado
    ocupado = True

    byte = 0
    if dec_manual or dec_ldr:
        # Con el decrementador activo, los LED muestran el resultado
        if dec_out & 0b100:
            byte |= LUZ_FRONTALES
        if dec_out & 0b010:
            byte |= DIR_IZQ | DIR_DER
        if dec_out & 0b001:
            byte |= LUZ_FRENO
    else:
        freno_activo = utime.ticks_diff(freno_hasta, utime.ticks_ms()) > 0
        if lb or oscuro or freno_activo:
            byte |= LUZ_FRENO
        if lf or oscuro or potencia < 0:
            byte |= LUZ_FRONTALES
        if ll and parpadeo:
            byte |= DIR_IZQ
        if lr and parpadeo:
            byte |= DIR_DER

    for i in range(7, -1, -1):
        DATA.value((byte >> i) & 1)
        CLK.value(1)
        CLK.value(0)

    ocupado = False


def tick(t):
    """Cada 500 ms: parpadeo, lectura del LDR, decrementador y refresco de luces."""
    global parpadeo, oscuro
    parpadeo = not parpadeo
    if ldr_auto:
        oscuro = ldr.read_u16() < UMBRAL_LDR
    else:
        oscuro = False
    if dec_ldr:
        actualizar_decrementador()
    if not ocupado:
        actualizar_luces()


# =====================================================
#  MOTOR 1: TRACCIÓN
# =====================================================
def set_pwm(valor):
    """valor de -4 a 4. Negativo = retroceder, 0 = detenido."""
    global potencia, freno_hasta
    valor = max(-4, min(4, int(valor)))

    if valor == 0 and potencia != 0:
        freno_hasta = utime.ticks_add(utime.ticks_ms(), T_FRENO)

    potencia = valor

    if valor > 0:
        in1.value(1)
        in2.value(0)
        ena.duty_u16(NIVELES[valor])
    elif valor < 0:
        in1.value(0)
        in2.value(1)
        ena.duty_u16(NIVELES[-valor])
    else:
        in1.value(0)
        in2.value(0)
        ena.duty_u16(0)

    actualizar_luces()


# =====================================================
#  MOTOR 2: DIRECCIÓN
# =====================================================
def aplicar_direccion(sentido, duty):
    """sentido: 1 = derecha, -1 = izquierda, 0 = motor libre."""
    if sentido == 1:
        in3.value(1)
        in4.value(0)
        enb.duty_u16(duty)
    elif sentido == -1:
        in3.value(0)
        in4.value(1)
        enb.duty_u16(duty)
    else:
        in3.value(0)
        in4.value(0)
        enb.duty_u16(0)


def mover_direccion(sentido, tiempo):
    aplicar_direccion(sentido, DUTY_GIRO)
    utime.sleep(tiempo)


def set_dir(valor):
    """-1 izquierda, 0 recto, 1 derecha, 2 retroceder."""
    global dir_actual
    if valor == 2:
        set_pwm(-2)
        return
    if valor not in (-1, 0, 1):
        print("dir debe ser -1, 0, 1 o 2")
        return

    pasos = valor - dir_actual
    if pasos != 0:
        mover_direccion(1 if pasos > 0 else -1, abs(pasos) * T_DIR)
        dir_actual = valor

    if valor == 0:
        aplicar_direccion(0, 0)
    else:
        aplicar_direccion(valor, DUTY_MANTENER)


# =====================================================
#  PATRONES
# =====================================================
def direccionales(izq, der):
    global ll, lr
    ll = izq
    lr = der
    actualizar_luces()


def circle():
    print("Patrón: Circle")
    direccionales(0, 1)
    set_dir(1)
    set_pwm(2)
    utime.sleep(T_CIRCULO)
    set_pwm(0)
    set_dir(0)
    direccionales(0, 0)


def infinite():
    print("Patrón: Infinite")
    direccionales(0, 1)
    set_dir(1)
    set_pwm(2)
    utime.sleep(T_CIRCULO)
    direccionales(1, 0)
    set_dir(-1)
    utime.sleep(T_CIRCULO)
    set_pwm(0)
    set_dir(0)
    direccionales(0, 0)


def zigzag():
    print("Patrón: ZigZag")
    set_pwm(2)
    for i in range(REP_ZIGZAG):
        if i % 2 == 0:
            direccionales(0, 1)
            set_dir(1)
        else:
            direccionales(1, 0)
            set_dir(-1)
        utime.sleep(T_ZIGZAG)
    set_pwm(0)
    set_dir(0)
    direccionales(0, 0)


def especial():
    print("Patrón: especial")
    set_pwm(2)
    utime.sleep(1)
    set_dir(1)
    utime.sleep(1)
    set_dir(-1)
    utime.sleep(1)
    set_dir(0)
    set_pwm(0)
    utime.sleep(0.5)
    direccionales(1, 1)
    set_pwm(-2)
    utime.sleep(2)
    set_pwm(0)
    direccionales(0, 0)


# =====================================================
#  COMANDOS
# =====================================================
def ejecutar(linea):
    """Interpreta un comando. Ejemplos: 'pwm 2', 'dir -1', 'lf 1', 'circle',
    'dec 5' (prueba manual del decrementador), 'decldr 1', 'decoff'."""
    global lf, lb, ll, lr, ldr_auto, oscuro
    global dec_manual, dec_ldr, dec_in

    partes = linea.strip().lower().split()
    if len(partes) == 0:
        return
    cmd = partes[0]

    # Comandos sin valor
    if cmd == "circle":
        circle()
        return
    if cmd == "infinite":
        infinite()
        return
    if cmd == "zigzag":
        zigzag()
        return
    if cmd == "especial":
        especial()
        return
    if cmd == "stop":
        apagar_todo()
        return
    if cmd == "decoff":
        dec_manual = False
        dec_ldr = False
        actualizar_luces()
        return

    # Comandos con valor
    if len(partes) < 2:
        print("Falta el valor del comando")
        return
    try:
        val = int(partes[1])
    except ValueError:
        print("Valor inválido")
        return

    if cmd == "pwm":
        set_pwm(val)
    elif cmd == "dir":
        set_dir(val)
    elif cmd == "lf":
        lf = 1 if val else 0
        actualizar_luces()
    elif cmd == "lb":
        lb = 1 if val else 0
        actualizar_luces()
    elif cmd == "ll":
        ll = 1 if val else 0
        actualizar_luces()
    elif cmd == "lr":
        lr = 1 if val else 0
        actualizar_luces()
    elif cmd == "ldr":
        ldr_auto = 1 if val else 0
        if not ldr_auto:
            oscuro = False
        actualizar_luces()
    elif cmd == "dec":
        # Programa de pruebas manuales: A,B,C = val (0-7)
        if 0 <= val <= 7:
            dec_manual = True
            dec_ldr = False
            dec_in = val
            actualizar_decrementador()
            actualizar_luces()
        else:
            print("dec debe estar entre 0 y 7")
    elif cmd == "decldr":
        # Decrementador con la fotorresistencia
        if val:
            dec_ldr = True
            dec_manual = False
            actualizar_decrementador()
        else:
            dec_ldr = False
        actualizar_luces()
    else:
        print("Comando no reconocido:", cmd)


# =====================================================
#  INICIO / APAGADO
# =====================================================
def apagar_todo():
    """Detiene motores, centra la dirección y apaga las luces."""
    global lf, lb, ll, lr, ldr_auto, oscuro, freno_hasta
    global dec_manual, dec_ldr
    set_pwm(0)
    set_dir(0)
    lf = lb = ll = lr = ldr_auto = 0
    dec_manual = False
    dec_ldr = False
    oscuro = False
    freno_hasta = 0
    actualizar_luces()


def iniciar():
    CLK.value(0)
    escribir_entrada(0)
    actualizar_luces()


# =====================================================
#  WIFI Y SOCKET
# =====================================================
def conectar_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(SSID, PASSWORD)
        while not wlan.isconnected():
            print("Esperando la conexion WiFi...")
            utime.sleep(1)
    print("Conectado exitosamente a la ip:", wlan.ifconfig()[0])


def conectar_servidor():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(SERVER_ADDRESS)
    s.settimeout(0.2)        # recv no bloquea para siempre
    print("Conectado al servidor", SERVER_ADDRESS)
    return s


def enviar(sock, texto):
    sock.send((texto + "\n").encode())


def bucle_comunicacion():
    """Se conecta al servidor, recibe comandos y reporta el decrementador.
    Si se pierde la conexión, reintenta."""
    while True:
        sock = None
        try:
            sock = conectar_servidor()
            enviar(sock, "HELLO desde la raspy")
            buf = b""
            ultimo = None

            while True:
                # --- Recepción de comandos ---
                try:
                    data = sock.recv(256)
                    if data == b"":
                        raise OSError("servidor cerró la conexión")
                    buf += data
                except OSError as e:
                    # 110 = ETIMEDOUT, 11 = EAGAIN -> simplemente no llegó nada
                    if not e.args or e.args[0] not in (110, 11):
                        raise

                while b"\n" in buf:
                    linea, buf = buf.split(b"\n", 1)
                    texto = linea.decode().strip()
                    if texto:
                        print("Comando:", texto)
                        ejecutar(texto)
                        enviar(sock, "ACK " + texto)

                # --- Reporte del decrementador ---
                if dec_manual or dec_ldr:
                    estado = (dec_in, dec_out, dec_ldr_val)
                else:
                    estado = None
                if estado != ultimo:
                    ultimo = estado
                    if estado is None:
                        enviar(sock, "DECOFF")
                    else:
                        enviar(sock, "DEC %d %d %d" % estado)

        except OSError as e:
            print("Error de conexión:", e)
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        utime.sleep(2)       # espera antes de reintentar


# =====================================================
#  PROGRAMA PRINCIPAL
# =====================================================
timer = Timer()
try:
    iniciar()
    conectar_wifi()
    timer.init(period=500, mode=Timer.PERIODIC, callback=tick)
    bucle_comunicacion()

except KeyboardInterrupt:
    pass
finally:
    timer.deinit()
    set_pwm(0)
    aplicar_direccion(0, 0)
    lf = lb = ll = lr = ldr_auto = 0
    dec_manual = False
    dec_ldr = False
    oscuro = False
    freno_hasta = 0
    actualizar_luces()
