# SOFIA — Detección visual de posturas

Este documento corresponde a la actividad de detección visual. Incluye:

1. La justificación del framework y de las herramientas elegidas.
2. El diseño de las cinco posturas y el comando que representan para SOFIA.
3. La descripción del script entregable.

---

## 1. Framework y herramientas seleccionadas

### 1.1 Resumen

| Elemento | Elección |
|---|---|
| Framework de detección | **MediaPipe Hand Landmarker** (Tasks API de Google) |
| Lenguaje | **Python 3.12** |
| IDE | **Visual Studio Code** |
| Librerías | `mediapipe` 1.0.1, `opencv` (cv2), `numpy` 2.5.2 |
| Herramientas | `venv` + `pip` (entorno y dependencias), Git (control de versiones) |

### 1.2 Framework: MediaPipe Hand Landmarker

MediaPipe Hand Landmarker es un modelo preentrenado de Google. Detecta la mano
en la imagen y estima **21 puntos de referencia (keypoints)** por mano: la
muñeca y cuatro articulaciones por dedo. La arquitectura tiene dos etapas:

1. Un detector de palma localiza la mano en la imagen.
2. Un modelo de landmarks estima los 21 puntos dentro de esa región.

En modo `VIDEO`, MediaPipe reutiliza la posición de la mano del cuadro
anterior y no repite la detección completa en cada cuadro. Esto reduce el
costo de cómputo y hace más estable el seguimiento.

Se eligió por los siguientes criterios:

- **Facilidad de implementación.** El modelo ya está entrenado y se
  distribuye en un solo archivo (`hand_landmarker.task`, ~7.8 MB). No hace
  falta recolectar datos ni entrenar nada. La API se reduce a crear el
  detector y llamar `detect_for_video()` en cada cuadro, lo que permite
  concentrar el trabajo en la lógica de las posturas.
- **Disponibilidad de librerías.** Se instala con `pip install mediapipe`.
  El mismo paquete instala OpenCV, que se usa para la cámara y el dibujo
  sobre el video. Tiene documentación oficial y una comunidad amplia.
- **Desempeño.** Funciona en tiempo real **solo con el CPU** de una laptop,
  sin necesidad de GPU. Esto es importante porque el sistema debe reaccionar
  de forma inmediata para que la interacción con el robot se sienta natural.
- **Compatibilidad con el proyecto.**
  - Funciona en Linux, macOS y Windows, así que cualquier integrante del
    equipo puede ejecutarlo.
  - Entrega coordenadas normalizadas (0–1). Estas se pueden traducir
    fácilmente a direcciones de mirada o a ángulos de los servos de SOFIA.
  - Python puede comunicarse después con la simulación en Unity
    (`sofia-simulation`) o con el microcontrolador mediante sockets o puerto
    serie.

#### Alternativas consideradas

| Alternativa | Motivo por el que se descartó |
|---|---|
| **OpenPose** | Requiere GPU para ir en tiempo real. Su instalación es compleja (compilación con CUDA/Caffe). |
| **YOLO-Pose (Ultralytics)** | Su modelo base es de cuerpo completo. Detectar keypoints de la mano requiere un modelo específico o entrenarlo, y el rendimiento en CPU es menor. |
| **MMPose** | Es muy flexible, pero pesado. Depende de PyTorch y de toda una cadena de configuración, lo cual es excesivo para este alcance. |
| **OpenCV solo (segmentación por color o contornos)** | Es sensible a la iluminación y al fondo. No entrega keypoints por articulación, así que no permite distinguir dedos de forma confiable. |

### 1.3 Lenguaje: Python 3.12

- Es el lenguaje principal de MediaPipe y OpenCV, y el mejor documentado
  para ambos.
- Permite iterar rápido: no hay compilación, y se cambia un umbral y se
  vuelve a probar en segundos.
- Se fijó la versión **3.12** porque MediaPipe suele tardar en publicar
  paquetes para las versiones más nuevas de Python. 3.12 está soportada
  oficialmente.

### 1.4 IDE: Visual Studio Code

- Es gratuito y multiplataforma.
- Su extensión de Python ofrece depuración, autocompletado y selección del
  intérprete del entorno virtual `.venv`.
- Es el mismo editor que se usa para los scripts C# de la simulación en
  Unity, así que el equipo trabaja con una sola herramienta.

### 1.5 Librerías

| Librería | Uso en el proyecto |
|---|---|
| `mediapipe` | Detección de la mano y estimación de los 21 keypoints. |
| `opencv` (`cv2`) | Captura de la cámara, efecto espejo, dibujo del esqueleto, los keypoints y el texto, y la ventana de video. |
| `numpy` | Cálculo de distancias, centroides y filtrado (media móvil exponencial). |

### 1.6 Otras herramientas

- **`venv` + `requirements.txt`:** el entorno queda aislado y las versiones
  fijas, de modo que cualquier integrante reproduce el mismo entorno con
  `pip install -r requirements.txt`.
- **`find_camera.py`:** identifica el índice de la cámara correcta cuando
  hay más de una.
- **Git:** control de versiones. El `.gitignore` excluye el entorno virtual
  y el modelo, que se descarga automáticamente.

---

## 2. Diseño de las cinco posturas

Se definieron **tres posturas estáticas** y **dos gestos dinámicos**:

- Las **estáticas** dependen de la forma de la mano, es decir, de qué dedos
  están extendidos.
- Las **dinámicas** dependen del movimiento o del cambio de forma de la
  mano a lo largo del tiempo.

Cada postura activa un movimiento distinto de SOFIA.

| # | Postura | Tipo | Qué representa | Acción de SOFIA |
|---|---|---|---|---|
| 1 | Dedo índice | Estática | Señalar | Mira hacia arriba y luego hacia abajo |
| 2 | "V" (índice + medio) | Estática | Seña de "dos" o de paz | Dibuja un 8 horizontal (∞) |
| 3 | Palma abierta | Estática | Saludo / "alto" | Mira a la derecha y luego a la izquierda |
| 4 | Deslizar (swipe) izquierda/derecha | Dinámica | Barrer o desplazar | Ola de movimiento en la dirección del gesto |
| 5 | Agarrar (palma → puño) | Dinámica | Tomar o cerrar | Sobresalto y regreso a la posición de reposo |

### 2.1 Postura 1: Dedo índice

- **Forma:** solo el índice extendido. Medio, anular y meñique flexionados.
  El pulgar no se toma en cuenta.
- **Qué representa:** señalar o apuntar. Es el gesto más básico para dirigir
  la atención.
- **Función en la interacción:** SOFIA **mira hacia arriba y luego hacia
  abajo**. Muestra su movimiento vertical y funciona como un "asentir":
  confirma que el robot reconoció al usuario.

### 2.2 Postura 2: "V" con los dedos

- **Forma:** índice y medio extendidos. Anular y meñique flexionados. El
  pulgar no se toma en cuenta.
- **Qué representa:** la seña de "dos" o de "paz". Es fácil de hacer y muy
  distinta visualmente de las otras.
- **Función en la interacción:** SOFIA **dibuja un 8 horizontal (∞)**. Es la
  trayectoria más compleja, porque combina movimiento horizontal y vertical.
  Sirve como demostración de la coordinación de sus módulos.

### 2.3 Postura 3: Palma abierta

- **Forma:** los cinco dedos extendidos.
- **Qué representa:** un saludo o la señal de "alto". Es el gesto
  universal para iniciar una interacción.
- **Función en la interacción:** SOFIA **mira a la derecha y luego a la
  izquierda**. Muestra su movimiento horizontal, como si "buscara" o
  "negara". Es la contraparte del movimiento vertical de la postura 1.

### 2.4 Postura 4: Deslizar (swipe) a la izquierda o a la derecha

- **Forma:** la mano, con cualquier forma, se desplaza rápido y en línea
  recta hacia un lado. Debe recorrer más de 2.5 veces el ancho de la palma
  en menos de 0.4 s, y el movimiento horizontal debe ser al menos el doble
  del vertical.
- **Qué representa:** barrer o desplazar, igual que al cambiar de página en
  una pantalla táctil.
- **Función en la interacción:** SOFIA ejecuta una **ola de movimiento en la
  dirección del gesto**, con sus módulos moviéndose en secuencia. El gesto
  distingue la dirección, así que en la práctica da dos comandos:
  `SWIPE_LEFT` y `SWIPE_RIGHT`.

### 2.5 Postura 5: Agarrar (palma abierta → puño)

- **Forma:** transición de palma abierta a puño cerrado. El puño debe
  aparecer menos de 0.6 s después de ver la palma abierta y mantenerse al
  menos 3 cuadros.
- **Qué representa:** tomar, cerrar o "apagar". Es un gesto con carácter de
  cierre.
- **Función en la interacción:** SOFIA hace un **sobresalto y regresa a su
  posición de reposo**. Sirve como comando para detenerse o reiniciar la
  interacción.

### 2.6 Criterios de diseño

- **Distinguibles entre sí.** Las tres posturas estáticas difieren en el
  número de dedos extendidos (1, 2 y 5). Esto minimiza las confusiones del
  clasificador.
- **Independientes de la rotación y de la distancia:**
  - Un dedo cuenta como extendido si su punta está al menos 15% más lejos de
    la muñeca que su articulación intermedia (PIP). Esta comparación funciona
    aunque la mano esté inclinada.
  - Los umbrales de movimiento se miden en "anchos de palma", no en píxeles.
    Así, el gesto funciona igual con la mano cerca o lejos de la cámara.
- **Sin activaciones accidentales:**
  - Las posturas estáticas solo se activan si la mano se mantiene **quieta
    durante 0.5 s** (se mueve menos de 0.3 anchos de palma). Por eso un
    swipe hecho con la palma abierta no activa también la postura "palma".
  - La etiqueta de la postura se filtra por **mayoría de votos** en los
    últimos 5 cuadros, lo que elimina el parpadeo de la clasificación.
  - Cada postura se activa **una sola vez** mientras se mantenga. Después
    hay un tiempo de espera de 1 a 2 s antes de poder repetirla.
- **Cómodas:** todas son gestos cotidianos. No requieren entrenamiento del
  usuario ni posiciones forzadas.

---

## 3. Script entregable

El sistema se compone de dos archivos:

- **`tracker.py`:** script principal.
- **`gestures.py`:** clasificador de posturas.

### 3.1 Qué muestra sobre el video en vivo

- **Esqueleto de la mano:** líneas blancas que unen los 21 keypoints según la
  anatomía de los dedos.
- **Keypoints:** un punto en cada una de las 21 articulaciones detectadas.
- **Centroide de la palma:**
  - un punto rojo pequeño para el valor sin filtrar;
  - un punto verde grande para el valor suavizado con media móvil
    exponencial.
  - La posición del centroide se muestra en píxeles y en coordenadas
    normalizadas.
- **Postura actual:** `INDEX`, `V`, `PALM`, `FIST` o `-` si no se reconoce
  ninguna.
- **Aviso de gesto:** cuando se reconoce uno de los cinco gestos, aparece
  durante 1.5 s un mensaje con el gesto y la acción de SOFIA. Por ejemplo:
  `V -> SOFIA: draw a horizontal 8`. El mismo mensaje se imprime en la
  consola.
- **Modo `--debug`:** muestra qué dedos detecta el sistema como extendidos.
  Sirve para comprobar visualmente la clasificación y ajustar los umbrales.

### 3.2 Ejecución

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python find_camera.py            # identificar la cámara (opcional)
python tracker.py --camera 0     # ejecutar la detección
python tracker.py --debug        # con información de depuración
```

Para salir se presiona `q`.

### 3.3 Cómo verificar el funcionamiento

1. Mostrar cada postura estática (índice, "V", palma) y mantenerla quieta
   medio segundo. Debe aparecer el aviso correspondiente una sola vez.
2. Hacer un swipe con la palma abierta en cada dirección. Debe aparecer
   `SWIPE_LEFT` o `SWIPE_RIGHT`, y **no** debe aparecer `PALM`.
3. Abrir la mano y cerrarla en puño. Debe aparecer `GRAB`.
4. Mover la mano lentamente mientras se mantiene una postura. No debe
   activarse ningún gesto.
