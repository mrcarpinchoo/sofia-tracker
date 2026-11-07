# SOFIA — Manejo de errores y registro de eventos

Este documento corresponde a la actividad de manejo de excepciones y errores
de la integración PC/ESP32. Describe qué fallas detecta la aplicación, cómo
se recupera de cada una y qué queda registrado como evidencia.

---

## 1. Principios

1. **Ningún error cierra la aplicación.** Cada hilo (cámara, enlace con el
   Maestro, interfaz) atrapa sus excepciones, las registra y continúa.
   Las excepciones no previstas llegan a un manejador global
   (`sys.excepthook` y `threading.excepthook`) que las registra como
   `EXCEPCION_NO_CONTROLADA`.
2. **Recuperación automática cuando es segura.** La cámara se reabre sola y
   el enlace con el Maestro se restablece solo. El control del panal se
   retoma solo, salvo cuando eso iría contra una orden de STOP.
3. **STOP tiene prioridad.** Un STOP del usuario (o de otro cliente) nunca
   se deshace automáticamente. Un STOP que no recibe respuesta se reenvía
   hasta que el Maestro lo confirma.
4. **Todo queda registrado.** Cada sesión escribe un archivo CSV con la hora,
   el evento, la acción tomada y el estado del sistema en ese momento.

---

## 2. Registro de eventos

Cada ejecución de `python -m gui` crea `logs/eventos_<fecha>_<hora>.csv`
(otra carpeta con `--log-dir`, ninguno con `--no-log`). Cada fila se escribe
al instante, así que el archivo se conserva aunque el programa termine de
forma inesperada. Se abre directamente en Excel.

| Columna | Contenido |
|---|---|
| `fecha_hora` | Fecha y hora con milisegundos. |
| `nivel` | `INFO`, `AVISO` o `ERROR`. |
| `origen` | `camara`, `maestro` o `gui`. |
| `evento` | Nombre del evento (tablas de la sección 3). |
| `detalle` | Qué ocurrió (comando, causa, duración de la falla…). |
| `accion` | Qué hizo el programa al respecto: la estrategia de manejo. |
| `contexto` | Condiciones en ese momento: estado de la detección, cámara, suavizado, enlace, quién controla SOFIA, modo del Maestro, latencia p95. |

Ejemplo:

```
fecha_hora,nivel,origen,evento,detalle,accion,contexto
2026-11-06 23:02:31.594,ERROR,maestro,ENLACE_PERDIDO,3 comandos seguidos sin respuesta,se consulta el estado cada 250 ms y se reconecta en cuanto responda,deteccion=running; camara=0; suavizado=si; maestro=sin_respuesta
2026-11-06 23:02:34.415,INFO,maestro,ENLACE_RESTABLECIDO,tras 4.4 s sin respuesta,se envía ARM para conservar o retomar el control,deteccion=running; camara=0; suavizado=si; maestro=en_linea
2026-11-06 23:02:34.466,INFO,maestro,CONTROL_RETOMADO,ARM aceptado tras restablecerse el enlace,los gestos y la mano vuelven a mover el panal,…
```

Las advertencias y errores también aparecen en la lista **Eventos** de la
ventana y en la barra de estado.

---

## 3. Eventos

### 3.1 Cámara (`origen = camara`)

| Evento | Nivel | Cuándo ocurre | Manejo |
|---|---|---|---|
| `CAMARA_NO_DISPONIBLE` | ERROR | Al pulsar **Iniciar**, la cámara elegida no abre (índice incorrecto, desconectada, ocupada por otro programa). | Se muestra un aviso, la detección queda detenida y la cámara liberada. El usuario elige otra cámara o la conecta y pulsa **Iniciar**. No se reintenta: suele ser un índice equivocado. |
| `CAMARA_PERDIDA` | ERROR | Durante la detección, 5 lecturas seguidas fallan (cámara desconectada o tomada por otro programa). | Estado *Reconectando cámara…*; se intenta reabrir cada 2 s indefinidamente. **Finalizar** cancela los reintentos. El panal deja de seguir la mano (sin objetivo, el Maestro mantiene la pose a los 0.5 s). |
| `CAMARA_RECUPERADA` | INFO | La cámara volvió a entregar cuadros. | Se reanuda la detección (o la pausa, si estaba en pausa) con el historial de gestos reiniciado, para que el salto de la mano no se confunda con un deslizamiento. |
| `ERROR_DETECCION` | ERROR | Falla de MediaPipe u otra excepción en la detección. | Se muestra un aviso y la detección se detiene limpiamente; la aplicación sigue abierta. |
| `GESTO_DETECTADO` | INFO | Se reconoció un gesto. | Se envía al Maestro si está conectado. Junto con `COMANDO_ACEPTADO` documenta el ciclo detección → comando → ACK. |

### 3.2 Enlace con el Maestro (`origen = maestro`)

| Evento | Nivel | Cuándo ocurre | Manejo |
|---|---|---|---|
| `COMANDO_ACEPTADO` | INFO | El Maestro confirmó (ACK) un comando del usuario o un gesto. | Se registra el tiempo de respuesta y los reenvíos. |
| `COMANDO_RECHAZADO` | AVISO | El Maestro respondió NACK (p. ej. `NOT_CONTROLLER`: otro cliente tiene el control o no se pulsó **Armar**; `BUSY`). | El comando se descarta (un NACK es definitivo según el protocolo). Se indica si hay que pulsar **Armar**. |
| `COMANDO_SIN_RESPUESTA` | AVISO | Un comando no recibió respuesta tras 3 reenvíos (200 ms cada uno). | El comando se descarta. |
| `ENLACE_PERDIDO` | ERROR | 3 comandos seguidos sin respuesta: ESP32 apagado, reiniciándose, fuera de alcance o la PC salió de la red. | El enlace se muestra *sin respuesta*. Se sigue consultando el estado cada 250 ms para detectar el regreso. |
| `ENLACE_RESTABLECIDO` | INFO | Llegó una respuesta tras una pérdida del enlace. | Si el usuario había pulsado **Armar**, se envía ARM de inmediato, antes de cualquier gesto pendiente (desde el controlador, ARM no cambia nada). |
| `CONTROL_RETOMADO` | INFO | El ARM automático fue aceptado. | Los gestos y la mano vuelven a mover el panal sin intervención. |
| `CONTROL_NO_RETOMADO` | AVISO | El enlace estuvo caído más de 30 s, u otro cliente tomó el control durante la caída (ARM rechazado). | No se rearma solo: tras una caída larga puede haber personas junto al panal. El usuario pulsa **Armar**. |
| `CONTROL_LIBERADO` | AVISO | Con el enlace activo, este programa perdió el control: otro cliente envió STOP o tomó el control. | No se rearma solo: STOP tiene prioridad. El usuario pulsa **Armar**. |
| `STOP_SIN_RESPUESTA` / `OFF_SIN_RESPUESTA` | ERROR | Un STOP u OFF no recibió respuesta. | Se reenvía hasta que el Maestro responda, antes que cualquier otro comando. Se registra una sola vez. |
| `STOP_CONFIRMADO` / `OFF_CONFIRMADO` | INFO | El Maestro respondió al STOP/OFF reenviado. | — |
| `GESTO_DESCARTADO` | INFO / AVISO | Un gesto pendiente fue reemplazado por uno más reciente, o se detectó con el enlace perdido. | No se envía: un gesto solo tiene sentido en el momento; reproducirlo segundos después al reconectar sería incorrecto. |
| `ERROR_RED` | ERROR | El sistema operativo rechaza el envío (p. ej. la PC se desconectó de la red Wi‑Fi `SOFIA-Maestro`). | Se reintenta cada 0.5 s; se registra una vez hasta que el envío vuelve a funcionar. |
| `ERROR_INTERNO` | ERROR | Una respuesta no se pudo interpretar (p. ej. tamaño incorrecto por versiones distintas de firmware). | Se descarta esa respuesta y el enlace continúa. Un mismo error repetido se registra una vez. |

Los rechazos de `SET_TARGET` (la posición de la mano, 25 por segundo) no se
registran: sin control son esperados y llenarían el archivo.

### 3.3 Aplicación (`origen = gui`)

| Evento | Nivel | Cuándo ocurre |
|---|---|---|
| `SESION_INICIADA` / `SESION_FINALIZADA` | INFO | Apertura y cierre de la ventana. Al cerrar se envía STOP si este programa tiene el control y se libera la cámara. |
| `DETECCION_INICIADA` / `DETECCION_FINALIZADA` | INFO | **Iniciar** / **Finalizar** (incluye la cámara y el suavizado usados). |
| `MAESTRO_CONECTADO` / `MAESTRO_DESCONECTADO` | INFO | Se marca o desmarca **Enviar gestos y mano al Maestro**. |
| `EXCEPCION_NO_CONTROLADA` | ERROR | Una excepción no prevista en la interfaz o en cualquier hilo. Se registra el tipo, el mensaje, el archivo y la línea; la aplicación sigue en ejecución. |

---

## 4. Lado del ESP32

El Maestro complementa este manejo (ver
`sofia-firmware/docs/PROTOCOL.md` §8):

- Si deja de recibir la posición de la mano, mantiene la pose a los 500 ms.
- Si el controlador no envía nada durante 3 s, libera el control; un gesto
  en curso termina.
- Las tramas corruptas (CRC, longitud) se descartan sin respuesta; el
  cliente se recupera con su tiempo de espera.
- Los comandos retransmitidos con el mismo `SEQ` no se ejecutan dos veces.

---

## 5. Cómo provocar cada falla en las pruebas

| Falla | Cómo provocarla |
|---|---|
| Cámara no disponible | Elegir un índice sin cámara y pulsar **Iniciar**. |
| Cámara perdida | Desconectar la cámara USB durante la detección, o abrirla en otro programa. Reconectarla. |
| Enlace perdido (corto) | Apagar y encender el ESP32, o alejar la PC, durante menos de 30 s. |
| Enlace perdido (largo) | Apagar el ESP32 más de 30 s. |
| PC fuera de la red | Desconectar el Wi‑Fi de la PC y volver a conectarlo. |
| Control tomado por otro cliente | Desde otra terminal: `python -m comms stop`, o **Armar** en `sofia-web`. |
| STOP sin respuesta | Apagar el ESP32 y pulsar **STOP**; encenderlo de nuevo. |
