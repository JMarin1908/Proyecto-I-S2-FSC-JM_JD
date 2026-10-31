import utime
from machine import Pin, PWM, ADC, Timer

# =====================================================
#  PINES
# =====================================================

# ---------- Motor 1: tracción (adelante / atrás) ----------
ena = PWM(Pin(16))          # ENA con PWM para controlar la potencia
ena.freq(1000)
ena.duty_u16(0)
in1 = Pin(17, Pin.OUT)
in2 = Pin(18, Pin.OUT)

# ---------- Motor 2: dirección (derecha / izquierda) ----------
enb = PWM(Pin(19))          # ENB con PWM para poder mantener la posición con menos potencia
enb.freq(1000)
enb.duty_u16(0)
in3 = Pin(20, Pin.OUT)
in4 = Pin(21, Pin.OUT)

# ---------- SN74LS164 (registro de desplazamiento) ----------
# Según el plano: A y B unidas -> GP15, CLK -> GP14
# CLR (pin 9) va conectado a 5V, por eso no se controla desde la Pico
DATA = Pin(15, Pin.OUT)     # entradas A y B unidas
CLK = Pin(14, Pin.OUT)      # CLK (pin 8 del integrado)

# ---------- Fotorresistencia (LDR) ----------
ldr = ADC(26)               # GP26 = ADC0 (verificar con el plano)

# =====================================================
#  CONSTANTES (ajustar probando el carro)
# =====================================================

# Potencia: niveles 1 a 4 -> duty de 0 a 65535
# (el mínimo ronda 500/1023 ~ 32000, depende de los motores)
NIVELES = {1: 32000, 2: 43000, 3: 54000, 4: 65535}

T_DIR = 0.4          # segundos que gira el motor de dirección por cada paso
DUTY_GIRO = 65535       # potencia del motor de dirección al moverse
DUTY_MANTENER = 55000   # potencia para sostener las llantas giradas 
T_FRENO = 1000       # ms que duran las luces de freno al detenerse
UMBRAL_LDR = 20000   # por debajo de este valor se considera "oscuro"

T_CIRCULO = 4        # segundos de avance para dar la vuelta circular
T_ZIGZAG = 1         # segundos de cada tramo del zig zag
REP_ZIGZAG = 4       # cantidad de tramos del zig zag

# Bits de salida del 74LS164 (QA = bit 0 ... QH = bit 7)
LUZ_FRENO = (1 << 0) | (1 << 1)      # QA y QB
LUZ_RETRO = (1 << 2) | (1 << 3)      # QC y QD
DIR_IZQ = 1 << 4                     # QE
DIR_DER = 1 << 5                     # QF

# =====================================================
#  VARIABLES DE ESTADO
# =====================================================
potencia = 0         # -4 a 4
dir_actual = 0       # -1 izquierda, 0 centro, 1 derecha
lf = 0               # luces frontales
lb = 0               # luces traseras
ll = 0               # direccional izquierda
lr = 0               # direccional derecha
ldr_auto = 0         # control automático por LDR
oscuro = False       # True si el LDR detecta poca luz
parpadeo = False     # cambia cada 500 ms (para las direccionales)
freno_hasta = 0      # instante hasta el que se mantiene el freno
ocupado = False      # evita que el timer interrumpa el envío al 74LS164


# =====================================================
#  LUCES (74LS164)
# =====================================================
def actualizar_luces():
    """Arma el byte de luces y lo envía al 74LS164."""
    global ocupado
    ocupado = True

    byte = 0
    freno_activo = utime.ticks_diff(freno_hasta, utime.ticks_ms()) > 0


if lb or oscuro or freno_activo:
        byte |= LUZ_FRENO
    # Luces frontales / retroceso (QC y QD): manual, por LDR o al retroceder
    if lf or oscuro or potencia < 0:
        byte |= LUZ_RETRO
    # Direccionales (QE y QF) intermitentes
    if ll and parpadeo:
        byte |= DIR_IZQ
    if lr and parpadeo:
        byte |= DIR_DER

    # Se envía primero el bit de QH y de último el de QA
    for i in range(7, -1, -1):
        DATA.value((byte >> i) & 1)
        CLK.value(1)
        CLK.value(0)

    ocupado = False


def tick(t):
    """Se ejecuta cada 500 ms: parpadeo, lectura del LDR y refresco de luces."""
    global parpadeo, oscuro
    parpadeo = not parpadeo
    if ldr_auto:
        oscuro = ldr.read_u16() < UMBRAL_LDR
    else:
        oscuro = False
    if not ocupado:
        actualizar_luces()


# =====================================================
#  MOTOR 1: TRACCIÓN
# =====================================================
def set_pwm(valor):
    """valor de -4 a 4. Negativo = retroceder, 0 = detenido."""
    global potencia, freno_hasta
    valor = max(-4, min(4, int(valor)))

    # Si estaba en movimiento y se detiene -> luces de freno
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
    """Gira el motor de dirección durante 'tiempo' segundos (sin soltarlo al final)."""
    aplicar_direccion(sentido, DUTY_GIRO)
    utime.sleep(tiempo)

def set_dir(valor):
    """-1 izquierda, 0 recto, 1 derecha, 2 retroceder.
    Si la dirección queda a la izquierda o a la derecha, el motor se mantiene
    energizado para que las llantas no regresen solas al centro."""
    global dir_actual
    if valor == 2:
        set_pwm(-2)                      # retroceder
        return
    if valor not in (-1, 0, 1):
        print("dir debe ser -1, 0, 1 o 2")
        return

    # Como no hay sensor de posición, se cuenta cuántos pasos
    # hay que girar desde la posición actual hasta la deseada
    pasos = valor - dir_actual
    if pasos != 0:
        mover_direccion(1 if pasos > 0 else -1, abs(pasos) * T_DIR)
        dir_actual = valor

    # Mantener o soltar el motor según la posición final
    if valor == 0:
        aplicar_direccion(0, 0)              # centro: se suelta el motor
    else:
        aplicar_direccion(valor, DUTY_MANTENER)   # girado: se sigue empujando

# =====================================================
#  PATRONES
# =====================================================
def direccionales(izq, der):
    global ll, lr
    ll = izq
    lr = der
    actualizar_luces()


def circle():
    """Vuelta circular y se detiene en el punto de partida."""
    print("Patrón: Circle")
    direccionales(0, 1)
    set_dir(1)
    set_pwm(2)
    utime.sleep(T_CIRCULO)
    set_pwm(0)
    set_dir(0)
    direccionales(0, 0)


def infinite():
    """Recorrido en forma de infinito (8 invertido)."""
    print("Patrón: Infinite")
    # Primer lazo hacia la derecha
    direccionales(0, 1)
    set_dir(1)
    set_pwm(2)
    utime.sleep(T_CIRCULO)
    # Segundo lazo hacia la izquierda
    direccionales(1, 0)
    set_dir(-1)
    utime.sleep(T_CIRCULO)
    set_pwm(0)
    set_dir(0)
    direccionales(0, 0)


def zigzag():
    """Zig zag durante un tiempo determinado."""
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
    """Patrón especial: avanza, hace una S, retrocede y baila con las luces."""
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
    direccionales(1, 1)          # luces de emergencia
    set_pwm(-2)                  # retroceder
    utime.sleep(2)
    set_pwm(0)
    direccionales(0, 0)


# =====================================================
#  COMANDOS
# =====================================================
def ejecutar(linea):
    """Interpreta un comando. Ejemplos: 'pwm 2', 'dir -1', 'lf 1', 'circle'."""
    global lf, lb, ll, lr, ldr_auto, oscuro

    partes = linea.strip().lower().split()
    if len(partes) == 0:
        return
    cmd = partes[0]

    # Patrones (sin valor)
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

    # Instrucciones de mando (con valor)
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
    else:
        print("Comando no reconocido:", cmd)


# =====================================================
#  INICIO / APAGADO
# =====================================================
def apagar_todo():
    """Detiene motores, centra la dirección y apaga las luces."""
    global lf, lb, ll, lr, ldr_auto, oscuro, freno_hasta
    set_pwm(0)
    set_dir(0)
    lf = lb = ll = lr = ldr_auto = 0
    oscuro = False
    freno_hasta = 0
    actualizar_luces()


def iniciar():
    # Como CLR está a 5V, el registro se limpia enviando 8 ceros
    # (actualizar_luces() manda los 8 bits con todas las luces apagadas)
    CLK.value(0)
    actualizar_luces()


# =====================================================
#  PROGRAMA PRINCIPAL
# =====================================================
timer = Timer()
try:
    iniciar()
    timer.init(period=500, mode=Timer.PERIODIC, callback=tick)

    print("Comandos: pwm [-4..4] | dir [-1,0,1,2] | lf/lb/ll/lr/ldr [1,0]")
    print("Patrones: circle | infinite | zigzag | especial | stop")

    while True:
        ejecutar(input("> "))


except KeyboardInterrupt:
    pass
finally:
    timer.deinit()
    set_pwm(0)
    aplicar_direccion(0, 0)      # libera el motor de dirección
    # Apagar luces
    lf = lb = ll = lr = ldr_auto = 0
    oscuro = False
    freno_hasta = 0
    actualizar_luces()