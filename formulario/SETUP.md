# SETUP — Formulario de campo ATC-20 (Firebase + Vercel)

Pasos de consola para dejar operativo el formulario (`formulario/`). Usa el
mismo proyecto Firebase del dashboard: **dagma-85aad**.

## 1. Habilitar Storage y verificar el bucket

1. Firebase console → **Storage** → *Get started*. Requiere plan **Blaze**
   (pago por uso; el free tier de Storage sigue aplicando).
2. Al crearse el bucket, copie su nombre exacto (aparece arriba, tipo
   `gs://dagma-85aad.firebasestorage.app`).
3. Verifique que coincida con `storageBucket` en
   `formulario/js/firebase-config.js`. Proyectos antiguos usan
   `dagma-85aad.appspot.com` — si es el caso, corrija el valor en ese archivo.

## 2. Crear la colección `inspectores`

Firestore no permite colecciones vacías, así que se crea con un doc plantilla:

1. Firestore → *Start collection* → ID de colección: `inspectores`.
2. Primer doc con ID `_plantilla` y estos campos (todos vacíos):

| Campo | Tipo | Valor |
|---|---|---|
| `nombre_completo` | string | `""` |
| `identificacion` | string | `""` |
| `profesion` | string | `""` |
| `num_telefono` | string | `""` |
| `entidad` | string | `""` |
| `email` | string | `""` |
| `codigo` | string | `""` |
| `consecutivo` | number | `0` |

## 3. Crear cada inspector real

Por cada inspector de campo:

1. **Authentication → Users → Add user**: correo + contraseña. Copie el **UID**
   generado.
2. **Firestore → inspectores → Add document**: el ID del doc es exactamente ese
   UID. Llene los campos de la plantilla con los datos reales:
   - `codigo`: código de brigada/usuario de **3 dígitos** como string, ej `"004"`.
   - `consecutivo`: `0` (el primer registro generará el consecutivo `0001`).
3. Verifique en el formulario: al iniciar sesión debe aparecer el nombre y la
   entidad en la barra superior. Si el doc no existe, el login se rechaza con
   "No está registrado como inspector".

## 4. Reglas de seguridad de Firestore

Firestore → *Rules*. Si ya hay reglas de otras colecciones (p. ej.
`inspecciones_israel`), **agregue estos bloques dentro del `match
/databases/{database}/documents` existente** en vez de reemplazar todo:

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {

    // Un usuario autenticado solo cuenta como inspector si tiene perfil.
    function isInspector() {
      return exists(/databases/$(database)/documents/inspectores/$(request.auth.uid));
    }

    // Inspector ACTIVO: además del perfil, el flag `activo` no debe ser false.
    // Un inspector inhabilitado desde el dashboard (tab Stickers) no puede
    // crear evaluaciones aunque su token siga vigente (~1h). `activo` ausente =
    // inspector antiguo = activo (compatibilidad hacia atrás).
    function isInspectorActivo() {
      return isInspector()
        && get(/databases/$(database)/documents/inspectores/$(request.auth.uid)).data.activo != false;
    }

    // Perfil de inspector: cada quien lee solo su doc; solo puede
    // actualizar su propio doc, únicamente el campo consecutivo, y solo
    // incrementándolo en exactamente 1.
    match /inspectores/{uid} {
      allow read: if request.auth != null && request.auth.uid == uid;
      allow update: if request.auth != null && request.auth.uid == uid
        && request.resource.data.diff(resource.data).affectedKeys().hasOnly(['consecutivo'])
        && request.resource.data.consecutivo is int
        && request.resource.data.consecutivo == resource.data.consecutivo + 1;
      allow create, delete: if false; // solo consola
    }

    // Evaluaciones: crear solo inspectores registrados y a nombre propio;
    // leer solo inspectores; nunca actualizar ni borrar (las
    // reinspecciones son un doc nuevo).
    match /evaluaciones/{id} {
      allow create: if isInspectorActivo()
        && request.resource.data.inspector.uid == request.auth.uid;
      allow read: if isInspector();
      allow update, delete: if false;
    }
  }
}
```

## 5. Reglas de seguridad de Storage

Storage → *Rules*:

```
rules_version = '2';
service firebase.storage {
  match /b/{bucket}/o {
    match /evaluaciones/{allPaths=**} {
      allow read: if firestore.exists(/databases/(default)/documents/inspectores/$(request.auth.uid));
      allow write: if firestore.exists(/databases/(default)/documents/inspectores/$(request.auth.uid))
        && request.resource.size < 15 * 1024 * 1024
        && request.resource.contentType.matches('image/.*');
    }
  }
}
```

Las consultas cross-service a Firestore (`firestore.exists(...)`) dentro de las
reglas de Storage están soportadas de forma nativa por Firebase rules v2.

## 6. Despliegue en Vercel (segundo proyecto, mismo repo)

1. Vercel → **Add New → Project** → importe este mismo repositorio.
2. En la configuración del proyecto:
   - **Root Directory**: `formulario`
   - **Build Command**: ninguno (sitio estático, sin build).
   - **Output Directory**: por defecto.
3. Deploy. Copie el dominio resultante (p. ej. `formulario-atc20.vercel.app`).
4. Firebase console → **Authentication → Settings → Authorized domains** →
   *Add domain* → pegue ese dominio. Sin este paso el login falla con
   `auth/unauthorized-domain`.

## Prueba rápida

1. Local: `npx serve formulario` (o `python -m http.server` dentro de la
   carpeta). `localhost` ya está autorizado en Firebase por defecto.
2. Inicie sesión con un inspector de prueba (codigo `"001"`, consecutivo `0`).
3. Seleccione área 1 y genere código → debe dar `76001-1-0010001`.
4. Elija la clasificación (INSPECCIONADA / USO RESTRINGIDO / INSEGURO) y el alcance.
5. Adjunte fotos y envíe → verifique el doc en `evaluaciones` y las fotos en
   Storage bajo `evaluaciones/{codigo}/`.
6. Un segundo registro debe generar `...0010002`.

## 7. Límite de fotos por registro (probado en apply de slice 2)

El signer externo (`https://sismo-fotos-signer.vercel.app/api/sign`) fue
probado en 2026-08-22 con `curl` (sign-only, sin subir ningún archivo real)
antes de habilitar más de 3 fotos en el cliente:

| Petición (`idToken` inválido a propósito) | `slot` | Respuesta |
|---|---|---|
| `POST /api/sign` | `1` | `401 {"error":"invalid-token"}` |
| `POST /api/sign` | `3` | `401 {"error":"invalid-token"}` |
| `POST /api/sign` | `4` | `400 {"error":"bad-request"}` |
| `POST /api/sign` | `10` | `400 {"error":"bad-request"}` |

`slot` 1 y 3 pasan la validación de esquema y fallan recién en la
autenticación (`401`, esperado con un token inválido de prueba); `slot` 4 y
10 son rechazados **antes** de validar el token (`400 bad-request`), es
decir el signer valida `slot` contra un rango `1..3` fijo en el servidor.
**Conclusión: el signer NO admite `slot > 3`.** El cliente queda configurado
con `MAX_FOTOS = 3` (fallback documentado en `design.md`, decisión "Slot-
Generic Design With Capped Fallback"). El resto de la funcionalidad de la
slice (selector de galería, cámara, grilla dinámica, subida en paralelo con
límite de concurrencia 3) se mantiene sin cambios; solo el tope visible de
slots baja de 10 a 3. Si el signer se actualiza para aceptar `slot` hasta
10, basta con cambiar la constante `MAX_FOTOS` en `formulario/js/logic.js`
— ningún otro archivo asume el valor 3.

## 8. Orden de despliegue y endurecimiento opcional (slice 3)

El cambio `stickers-form-upgrade` se entrega en tres PRs independientes,
cada uno revertible por separado (`git revert`), en este orden:

1. **Asignación de código** (consecutivo derivado de registros, segmento
   editable) — sin cambios de reglas ni de esquema.
2. **Captura de fotos** (galería/cámara, subida en paralelo, ver §7) — sin
   cambios de reglas.
3. **Sesión y rendimiento** (este slice: reintento con backoff ante fallas
   transitorias del perfil, un solo punto de importación de Firebase,
   `preconnect`/`modulepreload`) — sin cambios de reglas ni de esquema.

Ninguna de las tres requiere migración de datos ni despliegue de reglas
nuevas: la transacción `inspectores/{uid}` (incrementaba `consecutivo` en
cada clic de "Generar código") se elimina del cliente en el slice 1, pero el
campo permanece en el documento sin usarse — la regla de Firestore que la
protegía (`allow update` con `hasOnly(['consecutivo'])`, ver §4) sigue
desplegada y nunca se ejecuta desde el cliente nuevo.

**Endurecimiento opcional, post-rollout**: una vez confirmado en producción
que ningún cliente sigue escribiendo `inspectores/{uid}.consecutivo` (los
tres slices ya desplegados), la regla `allow update` de la sección `match
/inspectores/{uid}` en §4 puede reemplazarse por `allow update: if false;`
para cerrar por completo esa vía de escritura (dejando `allow create,
delete: if false;` como ya está). Esto es una limpieza de defensa en
profundidad, no un requisito de este cambio — el campo `consecutivo` de
`inspectores` ya no es leído por el cliente.

## 9. Resiliencia de sesión ante fallas transitorias (slice 3)

La verificación del perfil de inspector (`getDoc(inspectores/{uid})`) ahora
reintenta hasta 3 veces con backoff (600 ms, luego 1800 ms) antes de darse
por vencida. Solo un error clasificado como **fatal**
(`permission-denied`, `not-found`) cierra la sesión de inmediato — el
resto (`unavailable`, `deadline-exceeded`, `network-request-failed`,
cualquier código desconocido) se trata como transitorio: el inspector nunca
se desloguea por una falla de red pasajera en campo. Si los 3 intentos
fallan, la pantalla de acceso muestra un botón "Reintentar" en vez de forzar
el cierre de sesión.
