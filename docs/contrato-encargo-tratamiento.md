# Contrato de encargo de tratamiento (art. 28 RGPD)

> **BORRADOR** para el checklist legal del plan (§17). Debe revisarlo un
> abogado antes de firmarlo con el primer taxista. Los campos entre
> corchetes se rellenan por tenant en el onboarding.

En Madrid, a [FECHA].

## Partes

**Responsable del tratamiento**: [NOMBRE Y APELLIDOS DEL TAXISTA], NIF
[NIF], titular de la licencia [Nº LICENCIA] del Área de Prestación Conjunta
de Madrid (en adelante, el «Responsable»).

**Encargado del tratamiento**: [RAZÓN SOCIAL DEL PROVEEDOR, S.L.], NIF
[NIF], con domicilio en [DOMICILIO] (en adelante, el «Encargado»).

## 1. Objeto

El Encargado presta al Responsable una plataforma de gestión de reservas de
taxi con precio cerrado (formulario web de reserva, cálculo del precio,
emisión del justificante del art. 22 bis de la Ordenanza Reguladora del
Taxi, agenda y notificaciones). Para ello trata, por cuenta del Responsable,
los datos personales descritos en el anexo I.

## 2. Duración

La del contrato de suscripción al servicio. A su término, el Encargado
devolverá al Responsable los datos personales y los justificantes emitidos,
y suprimirá las copias en un plazo de 30 días, salvo los datos que deban
conservarse por obligación legal (bloqueados, durante los plazos legales).

## 3. Obligaciones del Encargado

El Encargado se compromete a:

1. Tratar los datos únicamente siguiendo las instrucciones documentadas del
   Responsable y para las finalidades del anexo I.
2. No utilizar los datos para fines propios ni cederlos a terceros salvo a
   los subencargados del anexo II o por obligación legal.
3. Garantizar la confidencialidad: el personal con acceso está sujeto a
   deber de secreto.
4. Aplicar las medidas de seguridad del anexo III (art. 32 RGPD).
5. Asistir al Responsable en la atención de los derechos de los interesados
   (acceso, rectificación, supresión, oposición, limitación, portabilidad),
   trasladándole sin dilación cualquier solicitud recibida.
6. Notificar al Responsable las violaciones de seguridad sin dilación
   indebida y, en todo caso, en un plazo máximo de 48 horas desde que tenga
   constancia, con la información del art. 33.3 RGPD.
7. Poner a disposición del Responsable la información necesaria para
   demostrar el cumplimiento y permitir auditorías razonables.
8. No realizar transferencias internacionales fuera del EEE sin garantías
   adecuadas (cláusulas contractuales tipo u otra base válida del cap. V
   RGPD); las existentes se declaran en el anexo II.

## 4. Subencargados

El Responsable autoriza de forma general la contratación de los
subencargados del anexo II. El Encargado comunicará con al menos 15 días de
antelación cualquier alta o sustitución, pudiendo el Responsable oponerse
por motivos justificados. El Encargado impone a cada subencargado las mismas
obligaciones de este contrato.

## 5. Obligaciones del Responsable

Corresponde al Responsable: facilitar la información del art. 13 RGPD a sus
clientes (la plataforma la muestra en el formulario de reserva y en la
política de privacidad), atender los derechos de los interesados, y mantener
su registro de actividades de tratamiento (art. 30 RGPD; modelo en el
anexo IV).

---

## Anexo I — Descripción del tratamiento

- **Interesados**: clientes (pasajeros) del Responsable.
- **Datos**: identificativos (nombre, teléfono, email opcional), datos de la
  reserva (origen, destino, fecha y hora), justificantes emitidos y
  notificaciones enviadas. Sin categorías especiales.
- **Finalidad**: gestión de reservas con precio cerrado, emisión del
  justificante obligatorio, y envío de confirmaciones, recordatorios y
  avisos de cancelación.
- **Operaciones**: recogida, registro, conservación, consulta, comunicación
  por email/push/SMS y supresión.
- **Conservación**: 4 años (normativa tributaria y de transporte), salvo
  plazo legal distinto.

## Anexo II — Subencargados autorizados

| Subencargado | Servicio | Ubicación de los datos |
|---|---|---|
| [PROVEEDOR DE HOSTING, p. ej. Hetzner Online GmbH] | Alojamiento (servidores y copias) | UE |
| Google Ireland Ltd. (Geocoding / Routes) | Geocodificación y cálculo de rutas (se envían direcciones, no la identidad del cliente) | UE/EE. UU. con CCT |
| [PROVEEDOR DE EMAIL, p. ej. Resend / AWS SES (región UE)] | Email transaccional | [UE / EE. UU. con CCT] |
| [PROVEEDOR DE SMS, si se activa] | SMS de recordatorio | [COMPLETAR] |

## Anexo III — Medidas de seguridad (resumen técnico)

- Cifrado en tránsito (TLS) en todo el sitio y las API.
- Aislamiento por tenant en base de datos; contraseñas con scrypt.
- Copias de seguridad diarias cifradas fuera del servidor de producción.
- Registro de accesos y de notificaciones enviadas; justificantes archivados
  con hash SHA-256.
- Minimización: sin cookies de terceros ni analítica externa.

## Anexo IV — Registro de actividades del Responsable (modelo art. 30)

- **Actividad**: gestión de reservas de taxi con precio cerrado.
- **Responsable**: [TAXISTA, NIF, contacto].
- **Finalidad**: ejecución del contrato de transporte y obligación legal
  (justificante art. 22 bis ORT).
- **Interesados y datos**: los del anexo I.
- **Destinatarios**: encargado (plataforma) y subencargados del anexo II;
  administraciones públicas cuando proceda.
- **Transferencias internacionales**: las del anexo II.
- **Plazos de supresión**: 4 años.
- **Medidas de seguridad**: las del anexo III.

---

Firmado por duplicado,

El Responsable — El Encargado
