# SOFIA — Reglas de detección y suavizado

Este documento complementa a `JUSTIFICACION.md`. Incluye:

1. Las reglas geométricas con las que se reconoce cada postura.
2. Las técnicas de suavizado que reducen el ruido y los falsos positivos.

---

## 1. Reglas geométricas de detección

### 1.1 Puntos utilizados

MediaPipe entrega 21 keypoints por mano. Las reglas usan estos:

| Índice | Nombre | Uso |
|---|---|---|
| 0 | Muñeca (`WRIST`) | Referencia para medir la extensión de los dedos. |
| 3 / 4 | Pulgar: IP / punta | Regla de extensión del pulgar. |
| 5 | Base del índice (`INDEX_MCP`) | Ancho de palma y centroide. |
| 6 / 8 | Índice: PIP / punta | Extensión del índice. |
| 9 | Base del medio | Centroide. |
| 10 / 12 | Medio: PIP / punta | Extensión del medio. |
| 14 / 16 | Anular: PIP / punta | Extensión del anular. |
| 17 | Base del meñique (`PINKY_MCP`) | Ancho de palma, centroide y regla del pulgar. |
| 18 / 20 | Meñique: PIP / punta | Extensión del meñique. |

Todas las distancias se calculan en **píxeles** y los umbrales de movimiento
se expresan en **anchos de palma**, para no depender de la distancia a la
cámara:

- **Ancho de palma:** $w_p = \max\left(\lVert \mathbf{p}_5 - \mathbf{p}_{17} \rVert,\ 1\right)$ (mínimo 1 px).
- **Centroide de la palma:** $\mathbf{c} = \dfrac{\mathbf{p}_0 + \mathbf{p}_5 + \mathbf{p}_9 + \mathbf{p}_{17}}{4}$.

### 1.2 Regla de extensión de los dedos

Un dedo (índice, medio, anular o meñique) está **extendido** si su punta
está más lejos de la muñeca que su articulación PIP:

$$
\lVert \mathbf{p}_{\text{punta}} - \mathbf{p}_0 \rVert \;>\; 1.15 \cdot \lVert \mathbf{p}_{\text{PIP}} - \mathbf{p}_0 \rVert
$$

El pulgar se mide contra la base del meñique, porque se abre hacia un lado
y no hacia arriba:

$$
\lVert \mathbf{p}_4 - \mathbf{p}_{17} \rVert \;>\; 1.1 \cdot \lVert \mathbf{p}_3 - \mathbf{p}_{17} \rVert
$$

Como solo usa razones entre distancias, la regla no cambia con la rotación
ni con el tamaño de la mano en la imagen.

### 1.3 Clasificación de la postura estática (etiqueta)

| Etiqueta | Índice | Medio | Anular | Meñique | Pulgar |
|---|---|---|---|---|---|
| `PALM` | ✔ | ✔ | ✔ | ✔ | ✔ |
| `FIST` | ✘ | ✘ | ✘ | ✘ | (no importa) |
| `INDEX` | ✔ | ✘ | ✘ | ✘ | (no importa) |
| `V` | ✔ | ✔ | ✘ | ✘ | (no importa) |
| ninguna (`None`) | cualquier otra combinación | | | | |

`FIST` es una etiqueta de apoyo: sirve de entrada a GRAB, pero no genera
un evento por sí sola. `PALM` genera su propio evento (§1.7) y además es el
inicio de GRAB.

### 1.4 Resumen de los cinco gestos

| # | Gesto | Tipo | Cuadros que usa | Acción de SOFIA |
|---|---|---|---|---|
| 1 | `INDEX` | Estática | Etiqueta estable y mano quieta durante 0.5 s | Mira arriba y luego abajo |
| 2 | `V` | Estática | Etiqueta estable y mano quieta durante 0.5 s | Dibuja un 8 horizontal |
| 3 | `PALM` | Estática | Etiqueta estable y mano quieta durante 0.5 s | Mira a la derecha y luego a la izquierda |
| 4 | `SWIPE_LEFT` / `SWIPE_RIGHT` | **Dinámica** | Ventana de 0.4 s | Ola de movimiento en la dirección del gesto |
| 5 | `GRAB` | **Dinámica** | Ventana de 0.6 s | Sobresalto y regreso al reposo |

**Dos de las cinco posturas son dinámicas** (SWIPE y GRAB). Su
reconocimiento necesita varios cuadros consecutivos: el desplazamiento del
centroide en el tiempo, o el cambio de forma de la mano de `PALM` a `FIST`.
Las tres estáticas (INDEX, V y PALM) también revisan una ventana corta, pero
solo para confirmar que la postura se mantiene.

Orden de evaluación en cada cuadro: **SWIPE → GRAB → estáticas**.
Solo se emite un evento por cuadro. Tras un evento dinámico se bloquea todo
el reconocimiento durante el tiempo de espera de ese gesto (§2.5).

### 1.5 Gesto 1: INDEX (estático)

Condiciones, todas a la vez:

- La etiqueta estable (§2.3) es `INDEX`.
- Todas las etiquetas de los últimos **0.5 s** son `INDEX`.
- El centroide se movió menos de **0.3 anchos de palma** respecto a cada
  posición de esos 0.5 s.
- Ya pasaron 2 s desde el último disparo de la misma postura.

Se dispara **una sola vez** mientras la postura se mantiene.

### 1.6 Gesto 2: V (estático)

Igual que INDEX, pero con la etiqueta `V`.

### 1.7 Gesto 3: PALM (estático)

Igual que INDEX, pero con la etiqueta `PALM` (los cinco dedos extendidos).

**Interacción con SWIPE.** Un swipe hecho con la palma abierta no activa
también PALM: la mano no está quieta, y al dispararse SWIPE se limpia el
historial.

**Interacción con GRAB.** GRAB empieza con la palma abierta. Si la persona
abre la mano y la mantiene quieta 0.5 s antes de cerrarla, primero se
dispara PALM y después GRAB. Si cierra la mano antes de 0.5 s, solo se
dispara GRAB. Si esto resulta un problema en las pruebas, se puede exigir a
PALM un tiempo de sostenimiento mayor que a INDEX y V.

### 1.8 Gesto 4: SWIPE (dinámico)

En una ventana de **0.4 s**, con $\Delta = \mathbf{c}_{\text{actual}} - \mathbf{c}_{\text{inicio}}$ (centroide
suavizado):

$$
|\Delta x| > 2.5\, w_p \qquad \text{y} \qquad |\Delta x| > 2\,|\Delta y|
$$

- Cualquier forma de mano sirve.
- La dirección sale del signo de $\Delta x$: positivo es `SWIPE_RIGHT` y negativo
  es `SWIPE_LEFT`. La imagen se muestra en espejo, así que "derecha" es la
  derecha del usuario.
- Tiempo de espera tras disparar: **1 s**.

### 1.9 Gesto 5: GRAB (dinámico)

- Las últimas **3** etiquetas crudas son `FIST`.
- En los últimos **0.6 s** apareció al menos una etiqueta `PALM` en el
  historial.
- Tiempo de espera tras disparar: **1 s**.

---

## 2. Suavizado

El ruido de los keypoints hace que un dedo parezca extendido y flexionado en
cuadros alternos, y que el centroide tiemble aunque la mano esté quieta. Se
aplican varias capas de filtrado. Todas están en `gestures.py`.

### 2.1 Media móvil exponencial (EMA)

$$
s_t = \alpha\, x_t + (1 - \alpha)\, s_{t-1}
$$

Con $\alpha = 1$ no hay filtrado. Cuanto menor es $\alpha$, más suave es la señal y
más se retrasa respecto a la mano real. Se usa en dos puntos:

| Filtro | Señal | $\alpha$ | Para qué |
|---|---|---|---|
| `LANDMARK_ALPHA` | Los 21 puntos en píxeles, **antes** de decidir qué dedos están extendidos | 0.5 | Evita que un dedo cambie de estado por un solo cuadro ruidoso |
| `CENTROID_ALPHA` | Centroide de la palma (regla de SWIPE y reposo) | 0.35 | Quita el temblor del movimiento sin borrar el trazo |

**Compromiso con el retardo.** Con $\alpha = 0.35$ el filtro retrasa la señal unos
2 cuadros (≈ 65 ms a 30 FPS). En SWIPE esto retrasa un poco el disparo, pero
no impide la detección, porque el trazo exigido (2.5 anchos de palma) es
mucho mayor que el temblor que se elimina.

### 2.2 Reinicio de los filtros

Si se pierde la mano, `reset()` borra los filtros, el historial y las
etiquetas. Así, el estado de una mano anterior no contamina a la siguiente.

### 2.3 Voto por mayoría de 5 cuadros

La etiqueta de la postura es la más frecuente entre las últimas
`DEBOUNCE_FRAMES` = 5 clasificaciones, y solo cuenta si tiene más de la
mitad de los votos. Un cuadro erróneo aislado no cambia la etiqueta.

### 2.4 Tiempo de sostenimiento y quietud

Las posturas estáticas exigen **0.5 s** con la misma etiqueta y con el
centroide quieto ($< 0.3\,w_p$). Una postura de paso, al cambiar de
una forma a otra, no llega a activarse.

### 2.5 Tiempos de espera (cooldown)

| Gesto | Espera |
|---|---|
| SWIPE | 1.0 s |
| GRAB | 1.0 s |
| INDEX / V / PALM (misma postura) | 2.0 s |

Tras un evento dinámico se bloquea todo el reconocimiento durante esa
espera y también se limpia el historial. Las estáticas solo bloquean que se
repita la misma postura. Así, un mismo movimiento no
genera dos eventos seguidos.

### 2.6 Efecto en los falsos positivos

| Técnica | Qué error reduce |
|---|---|
| EMA de landmarks | Cambios erráticos de un dedo entre extendido y flexionado |
| Voto por mayoría | Etiquetas que parpadean un solo cuadro |
| EMA del centroide | Temblor que se confunde con un swipe o que rompe la quietud de una postura estática |
| Tiempo de sostenimiento y quietud | Posturas de paso disparadas por accidente |
| Cooldown | Eventos duplicados por un mismo gesto |

### 2.7 Comparación con y sin suavizado

Tanto `tracker.py` como la GUI aceptan `--no-smooth`. Con esa opción
los dos filtros EMA pasan el valor sin cambios ($\alpha = 1$). El voto por
mayoría, el tiempo de sostenimiento y los cooldown se mantienen, porque son
parte de las reglas y no solo del suavizado.
