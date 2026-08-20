# Login del dashboard — configuración de Firebase

El dashboard ahora exige autenticación. Roles según **cómo** inicia sesión:

| Método de acceso | Rol | Qué ve |
|---|---|---|
| Google con correo `@cali.gov.co` | **viewer** | Solo **Panel**. Sin pestaña *Acciones*, sin botón *Actualizar datos*. |
| Usuario + contraseña (creados a mano) | **admin** | Todo: *Panel*, *Acciones* y *Actualizar datos*. |
| Google fuera de `@cali.gov.co` | — | Rechazado. |

Proyecto Firebase: **`dagma-85aad`**.

> **Estado:** ya quedó configurado y desplegado. La config web (`apiKey`/`appId`),
> los proveedores (Google + Email/Password) y los dominios autorizados de Vercel
> ya están puestos. Lo único que queda como paso manual de consola es el **nombre
> en la pantalla de consentimiento de Google** (ver sección final). El resto de
> esta guía es referencia por si hay que crear otro admin o rehacer el setup.

## 1. Pegar las credenciales web

Firebase console → ⚙ *Project settings* → *Your apps* → app web → *SDK setup and configuration*.
Copiá `apiKey` y `appId` en `web/js/firebase-config.js` (reemplazan los `PEGA_...`).
No son secretos: la apiKey web es un identificador público.

## 2. Habilitar proveedores de acceso

*Authentication → Sign-in method*:

- **Google** → *Enable*.
- **Email/Password** → *Enable* (con *Email link* apagado). Los admins se crean a mano en la pestaña *Users*.

## 3. Autorizar el dominio de producción (imprescindible)

*Authentication → Settings → Authorized domains* → *Add domain*:
agregá tu dominio de Vercel (p. ej. `sismo-cali.vercel.app`) y cualquier dominio propio.
Sin esto, el login con Google falla con `auth/unauthorized-domain`.
`localhost`, `dagma-85aad.firebaseapp.com` y `dagma-85aad.web.app` ya vienen autorizados.

## 4. Crear administradores

*Authentication → Users → Add user* (correo + contraseña). Esos son los que ven *Acciones* y *Actualizar datos*.

## 5. Nombre en la pantalla de consentimiento de Google (paso manual)

El popup de Google muestra "para continuar en …". Ese nombre se edita solo desde
la consola (no hay API pública estable para cambiarlo):

> Google Cloud Console → *APIs & Services* → *OAuth consent screen* → *Edit app* →
> **App name** → poné algo de la app, p. ej. `Visualización sísmica Cali` →
> *Save*. (También podés fijar un logo y el dominio de la app.)

Así deja de aparecer "Dagma".

## Seguridad — alcance real

- El botón *Actualizar datos* también se valida en el servidor (`api/refresh.js` verifica el ID token de Firebase y exige proveedor `password`): un viewer no puede dispararlo ni llamando al endpoint a mano.
- **Techo:** los JSON estáticos de `web/data/` siguen siendo descargables por URL directa. Esto restringe la UI y el disparo del refresh, no los archivos de datos crudos. Protegerlos requeriría servirlos detrás de funciones con verificación de token.
