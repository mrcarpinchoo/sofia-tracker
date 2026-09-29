# SOFIA — Reglas de detección, suavizado y evaluación

Este documento complementa a `JUSTIFICACION.md`. Incluye:

1. Las reglas geométricas con las que se reconoce cada postura.
2. Las técnicas de suavizado que reducen el ruido y los falsos positivos.
3. El protocolo de pruebas experimentales y las métricas de desempeño.

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

Tanto `tracker.py` como `evaluate.py` aceptan `--no-smooth`. Con esa opción
los dos filtros EMA pasan el valor sin cambios ($\alpha = 1$). El voto por
mayoría, el tiempo de sostenimiento y los cooldown se mantienen, porque son
parte de las reglas y no solo del suavizado. Las pruebas se repiten en los
dos modos para medir el efecto del suavizado.

---

## 3. Pruebas experimentales

### 3.1 Protocolo

| Elemento | Definición |
|---|---|
| Usuarios | 3 a 5 personas distintas (`U1`, `U2`, …) |
| Iluminación | `bright` (luz intensa), `normal`, `dim` (poca luz) y `backlight` (contraluz) |
| Repeticiones | 2 por gesto (`--trials 2`), en orden aleatorio: 6 gestos × 2 = 12 pruebas |
| Duración de cada prueba | 2 s de preparación y 4 s de registro |
| Segmento en reposo | 20 s moviendo la mano de forma natural, **sin** hacer gestos |
| Modos | Con suavizado y sin suavizado (`--no-smooth`) |

Notas:

- Cada usuario repite la sesión en las cuatro condiciones de luz.
- Además del brillo declarado, se mide el **brillo real** de cada sesión
  (promedio en escala de grises), como medida objetiva de la iluminación.
- SWIPE se cuenta como dos gestos (izquierda y derecha), por eso son seis
  tipos de prueba.
- El reconocimiento reinicia el estado entre pruebas para que no se
  afecten entre sí.

### 3.2 Métricas

| Métrica | Cómo se calcula |
|---|---|
| Tasa de acierto | Pruebas donde el **primer** evento fue el esperado ÷ total de pruebas |
| Tiempo de respuesta | Tiempo desde el inicio de la ventana de registro hasta el primer evento, solo en aciertos |
| FPS promedio | Cuadros procesados ÷ duración de la prueba |
| Tiempo de procesamiento | Milisegundos por cuadro (detección + reglas) |
| Falsos negativos | Pruebas donde no se detectó el gesto esperado |
| Falsos positivos | Gestos detectados que no eran los esperados, y eventos durante el segmento en reposo (se reportan por minuto) |
| Eventos extra | Eventos adicionales dentro de una misma prueba |
| Tasa de detección de mano | Cuadros con mano detectada ÷ total de cuadros |
| Matriz de confusión | Gesto esperado × gesto detectado, con `NONE` cuando no hubo evento |

El tiempo de respuesta incluye el tiempo que el usuario tarda en hacer el
gesto, así que incluye también el tiempo de sostenimiento (0.5 s en las
estáticas). Sirve para comparar modos, no como latencia pura del sistema.

### 3.3 Cómo ejecutar

```bash
source .venv/bin/activate

# Una sesión por usuario y condición de luz
python evaluate.py --user U1 --light normal --trials 2
python evaluate.py --user U1 --light normal --trials 2 --no-smooth
python evaluate.py --user U1 --light dim --trials 2
# ...

# Generar tablas y matriz de confusión con todos los CSV
python analyze.py
```

- `evaluate.py` espera la tecla `ESPACIO` para empezar. `q` aborta y
  conserva las filas ya guardadas.
- Cada sesión genera `results/<usuario>_<luz>_<smooth|nosmooth>_<fecha>.csv`,
  con una fila por prueba y una fila `IDLE` al final.
- `analyze.py` genera `results/summary.md` (tablas listas para pegar) y
  `results/confusion.png` (matriz de confusión).

### 3.4 Verificación previa con datos sintéticos

Antes de las pruebas con personas, se comprobó el reconocedor con manos
sintéticas (landmarks generados a 30 FPS con ruido de 1 px):

| Entrada | Resultado |
|---|---|
| Trazo rápido de 3.5 anchos de palma con la palma abierta | `SWIPE_RIGHT` (sin `PALM`) |
| Índice quieto durante más de 0.5 s | `INDEX` |
| Palma quieta durante 2 s | `PALM` una sola vez, a los 0.5 s |
| Palma durante 0.3 s y luego puño | `GRAB` |

Esto valida la lógica, no el desempeño con personas reales.

### 3.5 Resultados

> **Ronda preliminar.** Se hizo solo una parte del protocolo de §3.1:
> **un usuario** (`U1`), **las cuatro condiciones de luz**, **solo con
> suavizado** y **2 repeticiones por gesto**. En total son 48 pruebas
> (4 sesiones × 12) y 4 segmentos en reposo de 20 s. Faltan las sesiones
> con más usuarios y con `--no-smooth`. Por eso, estos resultados **no
> permiten** comparar usuarios ni medir el efecto del suavizado. Con 8
> pruebas por gesto, un solo error cambia la tasa de acierto en 12.5
> puntos.

Datos: `results/U1_{normal,bright,dim,backlight}_smooth_*.csv`. Las tablas
salen de `results/summary.md`, generado con `python analyze.py`. Las
medianas y los desgloses por luz se calcularon con los mismos CSV.

#### Tasa de acierto por gesto

| Gesto | Aciertos | Tasa de acierto | Falsos negativos |
|---|---|---|---|
| INDEX | 8/8 | 100 % | 0 |
| V | 8/8 | 100 % | 0 |
| PALM | 8/8 | 100 % | 0 |
| SWIPE_LEFT | 8/8 | 100 % | 0 |
| SWIPE_RIGHT | 8/8 | 100 % | 0 |
| GRAB | 7/8 | 87.5 % | 0 |
| **Total** | **47/48** | **97.9 %** | **0** |

El único error fue una prueba de GRAB (luz `bright`) en la que primero se
disparó `PALM` y después `GRAB`. Es justo la interacción descrita en §1.7:
la palma estuvo quieta 0.5 s antes de cerrarse. No hubo ninguna prueba sin
evento (`NONE`).

#### Tasa de acierto por usuario y por condición de iluminación

| Luz | Aciertos | Tasa de acierto | Brillo medido (0–255) | FPS promedio |
|---|---|---|---|---|
| `bright` | 11/12 | 91.7 % | 130.3 | 25.9 |
| `normal` | 12/12 | 100 % | 133.2 | 25.9 |
| `dim` | 12/12 | 100 % | 48.0 | **15.0** |
| `backlight` | 12/12 | 100 % | 111.2 | 27.2 |

Solo participó `U1` (47/48, 97.9 %), así que no hay comparación entre
usuarios.

Observaciones:

- **`bright` y `normal` son prácticamente la misma condición.** El brillo
  medido casi no cambia (130 contra 133). Lo más probable es que la
  exposición automática de la cámara compense la luz extra. El error en
  `bright` no se debe a la luz, sino a la interacción PALM → GRAB. Para
  que `bright` sea una condición distinta, hace falta una fuente de luz
  más intensa o fijar la exposición de la cámara.
- **Con poca luz, la cámara baja a 15 FPS.** El brillo medido fue 48. La
  precisión no bajó, pero el tiempo de respuesta sí subió (ver abajo).
- **El contraluz no afectó el reconocimiento.** Las 12 pruebas fueron
  correctas.

#### Tiempo de respuesta y FPS promedio

El tiempo de respuesta se mide desde el inicio de la ventana de registro,
así que incluye la reacción del usuario (§3.2). Algunas pruebas tardaron
más de 3 s porque el usuario empezó tarde. Por eso se reporta la
**mediana**, que no se deja arrastrar por esos casos, junto con la media
de `summary.md`.

| Gesto | Mediana | Media | Mín. – máx. |
|---|---|---|---|
| INDEX | 0.55 s | 0.55 s | 0.54 – 0.58 s |
| V | 0.57 s | 0.62 s | 0.55 – 0.97 s |
| PALM | 0.55 s | 1.23 s | 0.52 – 3.51 s |
| SWIPE_LEFT | 1.12 s | 1.24 s | 0.58 – 2.04 s |
| SWIPE_RIGHT | 1.25 s | 1.33 s | 0.77 – 2.04 s |
| GRAB | 0.52 s | 1.19 s | 0.23 – 3.39 s |
| **Todos** | **0.58 s** | **1.02 s** | |

| Luz | Mediana (todos) | Mediana (SWIPE) |
|---|---|---|
| `bright` | 0.56 s | 1.12 s |
| `normal` | 0.56 s | 0.92 s |
| `dim` | 1.23 s | 1.94 s |
| `backlight` | 1.04 s | 1.27 s |

| Métrica | Valor |
|---|---|
| FPS promedio | 23.5 (15.0 en `dim`, 25–27 en las demás) |
| Tiempo de procesamiento por cuadro | 6.4 ms en promedio (máx. 8.8 ms) |

- Las posturas estáticas responden en unos **0.55 s**, que coincide con
  el tiempo de sostenimiento de 0.5 s más un par de cuadros.
- El procesamiento (detección + reglas) toma **menos de 9 ms** por
  cuadro. Los FPS los limita la cámara, no el procesamiento.
- En `dim`, SWIPE tarda casi el doble (1.94 s contra ~1 s). A 15 FPS
  llegan menos cuadros durante el trazo, y la imagen se ve más borrosa.

#### Falsos positivos (segmento en reposo)

| Luz | Eventos en reposo | Mano visible en reposo | Eventos detectados |
|---|---|---|---|
| `bright` | 1 | 16 % (3.2 s) | INDEX |
| `normal` | 3 | 30 % (6.0 s) | PALM, GRAB, PALM |
| `dim` | 6 | 81 % (16.2 s) | PALM, GRAB, PALM, SWIPE_RIGHT, GRAB, GRAB |
| `backlight` | 0 | 0 % (0 s) | — |
| **Total** | **10** | **25.4 s de 80 s** | |

`summary.md` reporta **7.5 falsos positivos por minuto**, que resulta de
dividir 10 eventos entre 80 s. Pero ese número **subestima el problema**:
la cantidad de eventos sigue al tiempo que la mano estuvo en cámara, no a
la luz. En `backlight` la mano no apareció nunca, así que esa sesión no
midió nada. Tomando solo el tiempo con la mano visible, son 10 eventos en
25.4 s, es decir **unos 24 por minuto**.

Casi todos los falsos positivos son `PALM` y `GRAB`. Al mover la mano con
naturalidad, es fácil dejarla abierta y quieta medio segundo, o cerrarla
justo después. Posibles mejoras, pendientes de probar:

- Exigirle a PALM un tiempo de sostenimiento mayor (por ejemplo 1 s),
  como ya propone §1.7.
- Exigir que la palma esté de frente a la cámara, o cerca del centro de la
  imagen, antes de aceptar PALM o GRAB.
- Repetir el segmento en reposo pidiendo que la mano **se quede en
  cámara** los 20 s, para medir la tasa de forma comparable entre
  condiciones.

Además hubo **4 eventos extra** dentro de las pruebas: `GRAB` después de
`PALM` en dos pruebas de PALM (al bajar la mano cerrándola), el `GRAB` que
siguió al `PALM` en la prueba fallida de GRAB, y un `PALM` repetido. Los
eventos extra no cuentan para el acierto, porque solo cuenta el primer
evento.

#### Tasa de detección de la mano

| Luz | Posturas estáticas y GRAB | SWIPE |
|---|---|---|
| `bright` | 66 % | 23 % |
| `normal` | 51 % | 19 % |
| `dim` | 100 % | 30 % |
| `backlight` | 99 % | 22 % |

Esta métrica depende más de **cómo se hizo la prueba** que de la luz. En
SWIPE la mano entra y sale del cuadro, así que la tasa siempre es baja. En
las demás pruebas, la diferencia entre condiciones probablemente se debe a
si la mano se bajaba al terminar el gesto o se mantenía frente a la cámara,
algo que no se controló. Con estos datos **no se puede concluir** que la poca luz o el contraluz mejoren la detección. Para
medirla bien, la mano tendría que estar en cámara todo el tiempo de
registro.

#### Matriz de confusión

| esperado \ detectado | INDEX | V | PALM | SWIPE_LEFT | SWIPE_RIGHT | GRAB | NONE |
|---|---|---|---|---|---|---|---|
| INDEX | **8** | 0 | 0 | 0 | 0 | 0 | 0 |
| V | 0 | **8** | 0 | 0 | 0 | 0 | 0 |
| PALM | 0 | 0 | **8** | 0 | 0 | 0 | 0 |
| SWIPE_LEFT | 0 | 0 | 0 | **8** | 0 | 0 | 0 |
| SWIPE_RIGHT | 0 | 0 | 0 | 0 | **8** | 0 | 0 |
| GRAB | 0 | 0 | 1 | 0 | 0 | **7** | 0 |

![Matriz de confusión](../results/confusion.png)

#### Conclusiones preliminares

1. Con un solo usuario, el reconocedor acertó el **97.9 %** de las
   pruebas. Las posturas estáticas responden en unos 0.55 s y el
   procesamiento toma menos de 9 ms por cuadro.
2. La poca luz no bajó la precisión, pero redujo la cámara a 15 FPS y
   duplicó el tiempo de respuesta de SWIPE.
3. El punto débil son los **falsos positivos en reposo** (unos 24 por
   minuto con la mano en cámara), casi todos PALM y GRAB. Es lo primero
   que hay que ajustar.
4. La única confusión, GRAB → PALM, confirma la interacción prevista en
   §1.7.
5. Pendiente: más usuarios, la comparación con `--no-smooth` y un
   segmento en reposo con la mano siempre en cámara.
