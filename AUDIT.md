# AUDIT.md — Auditoría del código frente al modelo de listado neutro

Fecha: 22-07-2026 · Commit auditado: `a0a0a84` · 128 tests en verde.

---

## 1. Inventario técnico

**Stack**: Python 3.11/3.12 · FastAPI + Starlette (sesiones firmadas) ·
SQLAlchemy 2.0 (Mapped/mapped_column) · Jinja2 (render en servidor, JS
vanilla progresivo) · SQLite en desarrollo / PostgreSQL (psycopg 3) en
producción · pytest (128 tests) · Docker en Render (free tier).

**Dependencias clave**: httpx (integraciones), fpdf2 (hoja de ruta PDF),
segno (QR), pywebpush (Web Push), pydantic-settings (config por entorno
`TAXI_*`), WeasyPrint *opcional* (PDF del justificante; si no está, HTML).

**Estructura** (capas razonablemente separadas):

```
app/
  pricing/      dominio puro: motor de precio cerrado, tarifas versionadas,
                festivos (sin IO; todo Decimal)
  services/     casos de uso: cotizaciones, reservas, justificantes, bolsa,
                notificaciones, hoja de ruta PDF
  routing/      adaptadores de geocodificación/rutas: Photon→Nominatim con
                fallback y cuarentena, OSRM, Google (opcional), fake (tests)
  notificaciones/ adaptadores de canal: email (console/Brevo/Resend/SMTP),
                Telegram (Bot API con menús inline), Web Push
  api/          HTTP: API pública JSON, panel del taxista, webhook Telegram,
                cron
  web/          páginas del pasajero, cuentas, bolsa, perfiles,
                intermediarios; plantillas y estáticos (Leaflet vendorizado)
  models.py · db.py (mini-migraciones) · config.py · antifraude.py · seed.py
```

**Flujos existentes**:

1. **Reserva directa** `/t/{slug}`: formulario → cotización (precio máximo
   oficial con la política del taxista) → *solicitud pendiente* → el taxista
   acepta (igualar / descuento % / precio exacto, siempre ≤ máximo) o
   rechaza, desde su panel o con botones de Telegram → reserva + justificante
   → seguimiento del pasajero en `/s/{token}` → `/r/{token}`.
   Caducidad a los 20 min sin respuesta, con reenvío a la bolsa en un clic.
2. **Bolsa de viajes** `/viaje`: el pasajero (registrado) publica el trayecto
   con estimación oficial y **el primer taxista que acepta se lo lleva**
   (bloqueo de fila contra dobles asignaciones).
3. **Intermediarios** (hoteles): piden taxi para clientes; va a la bolsa.
4. **Panel del taxista**: agenda, reserva telefónica asistida, bolsa +
   pendientes, perfil (foto, bio, descuento, recogida, radio), QR, prueba de
   avisos, descarga de justificantes.
5. **Bot de Telegram**: vinculación de un toque, ubicación (modo Uber),
   radio configurable, conectar/desconectar, hoja de ruta en PDF con menú
   inline (aceptar / ajustar precio / mapa / rechazar).
6. **Justificante art. 22 bis ORT**: numeración correlativa por serie con
   bloqueo de fila, HTML archivado + copia en BD (disco efímero) + SHA-256,
   payload de cálculo persistido por reserva (`calculos_precio`).
7. **Cron por HTTP** (`/api/cron?token=`): recordatorios, caducidades.

**No existe** (mencionado en las instrucciones): chatbot de IA como
asistente de reserva; nada de WhatsApp (ya se cumple: canal propio web).

---

## 2. Contraste con las líneas rojas (sección 1 de las instrucciones)

| Línea roja | Estado | Detalle |
|---|---|---|
| No fijar precio desde la plataforma | ✅ Cumple | El motor calcula el **máximo oficial** (BOCM); el taxista decide el suyo ≤ máximo (descuento 0-30 % o precio exacto `precio_pactado`, validado contra el tope y registrado en el payload auditable). |
| No precios dinámicos al alza | ✅ Cumple | `precio_pactado > máximo` se rechaza con error. El único "alza" es el suplemento de recogida 0–5 €, que es un concepto **de la propia tarifa oficial** (servicios concertados), no un recargo de plataforma. |
| No asignar conductor | ⚠️ **Matiz importante** | En la reserva directa el pasajero elige taxista (✅). En la **bolsa de viajes** el pasajero NO elige: publica y *el primero que acepta gana*. No hay algoritmo de asignación (se autoseleccionan los taxistas), pero tampoco elección del pasajero. Es la pieza más cercana a la intermediación; el README ya lo advertía. **Decisión del titular** (ver §6). |
| No subasta / venta de leads | ✅ Cumple | Aceptación por orden de llegada, gratuita. Sin pujas. |
| No comisión sobre carrera / pago | ✅ Cumple | No hay pasarela de pago; el cobro es a bordo. No se factura nada por viaje. |
| No control de calidad que condicione | ⚠️ Revisar | Hay valoraciones ★ públicas en el perfil, pero **no** afectan al orden del listado (`/taxistas` ordena por fecha de alta) ni a ninguna asignación. Riesgo bajo; conviene dejarlo escrito como invariante (test) para que un cambio futuro no lo rompa. |
| No pasarela de pago del pasajero | ✅ Cumple | Inexistente. |

**Transparencia de con quién se contrata (4.2/6.3)**: el formulario y el
justificante identifican nombre, licencia y matrícula del taxista. Mejorable:
frase explícita «contratas con el taxista, no con la plataforma» en la oferta
y en el pie, y revisar que la marca del sitio no sugiera que la plataforma
transporta.

---

## 3. Deuda técnica

1. **Migraciones**: sistema propio mínimo (`ALTER TABLE ADD COLUMN`
   idempotente en `db.py`). Suficiente hasta ahora; Alembic recomendable
   antes de tocar columnas existentes o borrar.
2. **Rate limiting en memoria por proceso** (`antifraude.py`): se resetea en
   cada deploy y no funcionaría con varios workers. Aceptable en el MVP.
3. **Archivos en disco efímero**: los justificantes ya se regeneran desde la
   BD, pero **las fotos de perfil se pierden en cada deploy** (queda el
   placeholder; sin error).
4. **Tarifas como módulo Python** (`app/pricing/tarifas.py`): versionadas y
   separadas del motor (bien), pero las instrucciones piden actualizables
   *sin tocar código* → mover a fichero de datos (JSON/TOML) seleccionable
   por configuración. Cambio contenido y de bajo riesgo.
5. **Sin CI**: los tests corren en local; falta GitHub Actions que los pase
   en cada push.
6. **Secretos**: ninguno en el repositorio ni en el historial de git
   (verificado con búsqueda sobre `git log -p`). ⚠️ Pero el token del bot,
   el secreto del webhook y una URL de Postgres con credenciales se
   compartieron por chat durante el desarrollo: **rotación pendiente** por
   parte del titular.
7. **Duplicaciones**: purgadas en la limpieza del commit `2534cb4`; sin
   funciones muertas conocidas a fecha de hoy (ruff limpio en F401/F811
   salvo el patrón estándar de fixtures de pytest).
8. **Zonas horarias**: la compensación del tzinfo que pierde SQLite está
   repetida en varios puntos; candidata a helper único.
9. **Infra Render free**: el Postgres gratuito caduca (~30 días) — riesgo de
   pérdida total de datos; sin backups automatizados.

---

## 4. Cumplimiento (sección 6) — qué hay y qué falta

### 6.1 ORT art. 47 — datos y exportación → **FALTA (parcial)**
- ✅ Se persisten: reservas con características completas, cálculos,
  justificantes, notificaciones, y las **demandas no atendidas** existen
  implícitamente (solicitudes `caducada`/`rechazada`/`abierta` sin aceptar).
- ❌ No hay modelo de **quejas/reclamaciones**.
- ❌ No hay **exportación por rango de fechas** (CSV/PDF).

### 6.2 Eurotaxi / PMR → **AUSENTE, sin decisión**
- No se listan vehículos adaptados ni se publicita PMR (de facto, opción
  «NO»), pero falta el aviso claro y el *feature flag* que piden las
  instrucciones. **Decisión del titular pendiente.**

### 6.3 DSA art. 30 — trazabilidad del profesional → **FALTA**
- El alta del taxista valida formato (NIF, licencia numérica, contraseña)
  pero **no verifica identidad ni habilitación**, y el taxista queda listado
  inmediatamente. Falta: estado «pendiente de verificación» (no listado
  hasta aprobar), almacenamiento de la documentación acreditativa, y un
  mecanismo de notificación/retirada. Los términos y condiciones no existen
  como página propia.

### 6.4 RGPD / LOPDGDD → **PARCIAL**
- ✅ Política de privacidad por taxista enlazada en el punto de recogida del
  dato, aviso legal, cookies (sin banner: no hay terceros), plantilla de
  contrato de encargo art. 28 en `docs/`.
- ❌ Sin política de **retención/borrado** (nada se purga jamás).
- ❌ Si el modelo es corresponsabilidad, tocaría art. 26 (revisión letrada
  pendiente, como ya estaba anotado).
- Cifrado: en tránsito sí (HTTPS); en reposo depende del proveedor de BD.

### 6.5 Accesibilidad → **PARCIAL**
- Base razonable: formularios con `label`, roles ARIA en avisos, canal 100 %
  texto, sin JS obligatorio. Falta una pasada WCAG sistemática (contraste,
  foco, navegación por teclado en el autocompletar y los mapas).

### 6.6 VeriFactu para suscripciones → **AUSENTE**
- No existe facturación de la suscripción (el campo `estado_suscripcion`
  existe pero nada factura). Correcto por omisión: no se factura ninguna
  carrera. Cuando se cobre suscripción real, ese flujo deberá ser VeriFactu.

### Sección 5 — motor de precio cerrado
- ✅ Máximo oficial con tabla versionada 2026, T1/T2 con festivos y cambio de
  tarifa en ruta, redondeo 0,05, NO₂, Navidad, peaje consentido; tope duro
  del precio pactado; payload auditable persistido. 23 tests del motor.
- ❌ **Servicios excluidos**: el motor NO detecta hoy los trayectos con
  tarifa fija de aeropuerto (T3/T4) ni otros supuestos excluidos — la
  restricción está solo documentada en un comentario. Un pasajero puede
  pedir hoy un precio cerrado a Barajas y el sistema se lo da con T1/T2.
  **Es el hueco funcional más serio detectado.**

---

## 5. Otros hallazgos

- **Precio «estimado» de la bolsa**: se calcula con las tarifas oficiales
  genéricas (sin la política del taxista); al aceptar, el precio real lo
  emite el motor del taxista aceptante y puede diferir levemente del
  estimado mostrado. Conviene etiquetarlo aún más claramente como
  «orientativo» en la página del pasajero.
- La reserva telefónica asistida del panel crea la reserva al instante (la
  hace el propio taxista: coherente con el modelo).
- El chatbot IA de las instrucciones (4.1) no existe; el formulario con
  autocompletado cubre el flujo. Proponer como mejora opcional, no bloqueo.

---

## 6. Decisiones que corresponden al titular (no se implementa nada sin respuesta)

1. **Bolsa de viajes**: (a) mantener como está («primero que acepta»),
   (b) convertirla en *listado de ofertas* donde los taxistas se postulan y
   **el pasajero elige** (encaja mejor en Star Taxi App), o (c) retirarla
   del lanzamiento. Recomendación técnica: (b).
2. **Eurotaxi/PMR**: ¿se incluye o no? Si no, basta aviso + exclusión; si
   sí, hay que construir el bloque completo (≥24 h, certificados Anexo II,
   reporte) tras feature flag.
3. **Verificación DSA**: ¿revisión manual por el titular (panel de
   aprobación) o servicio externo de KYC? La primera es gratis y suficiente
   para empezar.
4. **Retención RGPD**: fijar plazos (propuesta: anonimizar datos de pasajero
   a los 12 meses de la recogida; conservar justificantes y payloads sin
   datos personales el plazo fiscal).

## 7. Plan propuesto tras la validación (orden de las instrucciones §8)

1. Externalizar la tabla tarifaria a fichero de datos + carga por config.
2. Motor: detección de **servicios excluidos** (aeropuerto/tarifa fija) con
   tests; invariante «el listado no ordena por valoración» como test.
3. Art. 47: modelo de quejas + exportación CSV por rango de fechas.
4. DSA: alta con estado `pendiente_verificacion` + panel de aprobación del
   titular + página de términos y condiciones.
5. Transparencia: frase «contratas con el taxista» en oferta y justificante.
6. RGPD: tarea de retención/anonimizado en el cron.
7. PMR: aviso + flag según decisión. Bolsa: según decisión.
