import { test, expect } from '@playwright/test';
import { mockFirebase, initBackendScript, defaultSeed, GSTATIC } from './firebase-mock.js';

const CREDS = { email: 'inspector@cali.gov.co', password: 'secret123' };

// Boot the page with an in-memory Firebase backed by `seed`. Must run before goto.
async function boot(page, seed = defaultSeed()) {
  await mockFirebase(page);
  await page.addInitScript(initBackendScript(seed));
  await page.goto('/');
}

async function login(page, creds = CREDS) {
  await page.fill('#auth-email', creds.email);
  await page.fill('#auth-password', creds.password);
  await page.click('#auth-submit');
}

async function loginAndWaitForm(page) {
  await login(page);
  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#geo-display')).toContainText('Lat:');
}

const PNG_1x1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
  'base64',
);

test.describe('Autenticación', () => {
  test('rechaza credenciales inválidas y mantiene el formulario oculto', async ({ page }) => {
    await boot(page);
    await login(page, { email: CREDS.email, password: 'malísima' });
    await expect(page.locator('#auth-error')).toHaveText('Correo o contraseña incorrectos.');
    await expect(page.locator('#app')).toBeHidden();
  });

  test('rechaza a un usuario autenticado sin perfil de inspector', async ({ page }) => {
    const seed = defaultSeed();
    seed.firestore.inspectores = {}; // el usuario existe en Auth pero no hay doc de inspector
    await boot(page, seed);
    await login(page);
    await expect(page.locator('#auth-error'))
      .toHaveText('No está registrado como inspector. Contacte a la coordinación.');
    await expect(page.locator('#app')).toBeHidden();
  });

  test('login exitoso muestra el formulario con nombre y entidad del inspector', async ({ page }) => {
    await boot(page);
    await loginAndWaitForm(page);
    await expect(page.locator('#inspector-nombre')).toHaveText('Ana Ruiz');
    await expect(page.locator('#inspector-entidad')).toHaveText('SGRED');
  });
});

test.describe('Código de la edificación', () => {
  test('exige seleccionar el área antes de generar', async ({ page }) => {
    await boot(page);
    await loginAndWaitForm(page);
    await page.click('#btn-codigo');
    await expect(page.locator('#codigo-error')).toHaveText('Seleccione el área antes de generar el código.');
  });

  test('genera el primer código como 76001-1-0040001 y bloquea el área', async ({ page }) => {
    await boot(page);
    await loginAndWaitForm(page);
    await page.selectOption('#area', '1');
    await page.click('#btn-codigo');
    await expect(page.locator('#codigo-prefijo')).toHaveText('76001-1-004');
    await expect(page.locator('#codigo-consecutivo')).toHaveValue('0001');
    await expect(page.locator('#area')).toBeDisabled();
    await expect(page.locator('#btn-codigo')).toBeDisabled();
  });
});

test.describe('Consecutivo derivado de registros', () => {
  test('con un salto en los registros previos, el siguiente código es 0004', async ({ page }) => {
    const seed = defaultSeed();
    seed.firestore.evaluaciones = {
      '76001-1-0040001': { inspector: { uid: 'uid-004' } },
      '76001-1-0040003': { inspector: { uid: 'uid-004' } },
    };
    await boot(page, seed);
    await loginAndWaitForm(page);
    await page.selectOption('#area', '1');
    await page.click('#btn-codigo');
    await expect(page.locator('#codigo-prefijo')).toHaveText('76001-1-004');
    await expect(page.locator('#codigo-consecutivo')).toHaveValue('0004');
  });

  test('abandonar el formulario sin enviar no consume un número (no hay escritura en Firestore)', async ({ page }) => {
    await boot(page);
    await loginAndWaitForm(page);
    await page.selectOption('#area', '1');
    await page.click('#btn-codigo');
    await expect(page.locator('#codigo-prefijo')).toHaveText('76001-1-004');
    // Generar código es solo lectura + caché local; nada se escribe hasta
    // enviar (la vieja transacción inspectores/{uid} que incrementaba en cada
    // clic ya no existe).
    const inspectorDoc = await page.evaluate(() => window.__fb.firestore.inspectores['uid-004']);
    expect(inspectorDoc.consecutivo).toBe(0);
    const evaluaciones = await page.evaluate(() => window.__fb.firestore.evaluaciones);
    expect(Object.keys(evaluaciones)).toHaveLength(0);
  });
});

test.describe('Segmento editable del código', () => {
  test('editar el segmento cambia el código final que se guarda (doc id + consecutivo numérico)', async ({ page }) => {
    await boot(page);
    await loginAndWaitForm(page);
    await page.selectOption('#area', '1');
    await page.click('#btn-codigo');
    await expect(page.locator('#codigo-consecutivo')).toHaveValue('0001');

    await page.fill('#codigo-consecutivo', '0005');
    await page.locator('input[name="clasificacion"][value="INSPECCIONADA"]').check();
    await page.locator('input[name="alcance"][value="exterior"]').check();
    await addFoto(page);
    await page.click('#btn-submit');

    await expect(page.locator('#confirm')).toBeVisible();
    await expect(page.locator('#confirm-codigo')).toHaveText('76001-1-0040005');

    const written = await page.evaluate(() => window.__fb.firestore.evaluaciones['76001-1-0040005']);
    expect(written).toBeTruthy();
    expect(written.consecutivo).toBe(5);
  });
});

// Attach a 1x1 PNG to the given photo slot (photos are mandatory on submit).
async function addFoto(page, slot = 0) {
  await page.locator('.foto-slot').nth(slot).locator('input[type="file"]')
    .setInputFiles({ name: `foto${slot + 1}.png`, mimeType: 'image/png', buffer: PNG_1x1 });
}

test.describe('Validación de envío', () => {
  test('no permite enviar sin generar el código', async ({ page }) => {
    await boot(page);
    await loginAndWaitForm(page);
    await page.locator('input[name="clasificacion"][value="INSPECCIONADA"]').check();
    await page.locator('input[name="alcance"][value="exterior"]').check();
    await page.click('#btn-submit');
    await expect(page.locator('#submit-error')).toHaveText('Genere el código de la edificación antes de enviar.');
    await expect(page.locator('#confirm')).toBeHidden();
  });

  test('no permite enviar sin al menos una foto', async ({ page }) => {
    await boot(page);
    await loginAndWaitForm(page);
    await page.selectOption('#area', '1');
    await page.click('#btn-codigo');
    await expect(page.locator('#codigo-prefijo')).toHaveText('76001-1-004');
    await expect(page.locator('#codigo-consecutivo')).toHaveValue('0001');
    await page.locator('input[name="clasificacion"][value="INSPECCIONADA"]').check();
    await page.locator('input[name="alcance"][value="exterior"]').check();
    await page.click('#btn-submit');
    await expect(page.locator('#submit-error'))
      .toHaveText('Agregue al menos una foto de la edificación antes de enviar.');
    await expect(page.locator('#confirm')).toBeHidden();
  });
});

test.describe('Flujo completo de registro', () => {
  test('registra una evaluación y persiste el documento y la foto', async ({ page }) => {
    await boot(page);
    await loginAndWaitForm(page);

    await page.selectOption('#area', '1');
    await page.click('#btn-codigo');
    await expect(page.locator('#codigo-prefijo')).toHaveText('76001-1-004');
    await expect(page.locator('#codigo-consecutivo')).toHaveValue('0001');

    await page.fill('#nombre', 'Colegio San José');
    await page.fill('#direccion', 'Calle 5 #10-20');

    await page.locator('input[name="clasificacion"][value="USO_RESTRINGIDO"]').check();
    await page.locator('input[name="alcance"][value="exterior_interior"]').check();
    await page.fill('#restricciones', 'No ingresar al tercer piso.');
    await page.locator('.acciones-card > summary').click();
    await page.check('#barricadas');

    await page.locator('.foto-slot').first().locator('input[type="file"]')
      .setInputFiles({ name: 'foto1.png', mimeType: 'image/png', buffer: PNG_1x1 });

    await page.click('#btn-submit');

    await expect(page.locator('#confirm')).toBeVisible();
    await expect(page.locator('#confirm-codigo')).toHaveText('76001-1-0040001');

    const written = await page.evaluate(() => window.__fb.firestore.evaluaciones['76001-1-0040001']);
    expect(written).toBeTruthy();
    expect(written.municipio).toBe('76001');
    expect(written.area).toBe(1);
    expect(written.area_nombre).toBe('Cabecera');
    expect(written.clasificacion).toBe('USO_RESTRINGIDO');
    expect(written.alcance).toBe('exterior_interior');
    expect(written.inspector.codigo).toBe('004');
    expect(written.inspector.uid).toBe('uid-004');
    expect(written.descripcion.direccion).toBe('Calle 5 #10-20');
    expect(written.coords).toBeTruthy();
    expect(typeof written.coords.lat).toBe('number');
    expect(written.fotos).toHaveLength(1);
    expect(written.fotos[0]).toBe('https://s3.mock/evaluaciones/76001-1-0040001/foto_1.jpg');
    expect(written.acciones_posteriores.barricadas).toBe(true);
    expect(written.consecutivo).toBe(1);
  });

  test('el consecutivo avanza en el segundo registro', async ({ page }) => {
    await boot(page);
    await loginAndWaitForm(page);

    // Primer registro completo.
    await page.selectOption('#area', '1');
    await page.click('#btn-codigo');
    await expect(page.locator('#codigo-prefijo')).toHaveText('76001-1-004');
    await expect(page.locator('#codigo-consecutivo')).toHaveValue('0001');
    await page.locator('input[name="clasificacion"][value="INSPECCIONADA"]').check();
    await page.locator('input[name="alcance"][value="exterior"]').check();
    await addFoto(page);
    await page.click('#btn-submit');
    await expect(page.locator('#confirm')).toBeVisible();

    // Nuevo registro → el área y el botón vuelven a estar habilitados.
    await page.click('#btn-nuevo');
    await expect(page.locator('#app')).toBeVisible();
    await expect(page.locator('#area')).toBeEnabled();
    await page.selectOption('#area', '1');
    await page.click('#btn-codigo');
    await expect(page.locator('#codigo-prefijo')).toHaveText('76001-1-004');
    await expect(page.locator('#codigo-consecutivo')).toHaveValue('0002');
  });
});

test.describe('Recuperación ante código duplicado', () => {
  test('si el código editado ya existe, avisa, conserva los datos y ofrece un código nuevo', async ({ page }) => {
    const seed = defaultSeed();
    // Colisión con un código creado fuera de la consulta de este inspector
    // (sin inspector.uid), como lo exige la generación por edición manual o
    // la creación concurrente desde otro dispositivo.
    seed.firestore.evaluaciones['76001-1-0040005'] = { codigo_edificacion: '76001-1-0040005' };
    await boot(page, seed);
    await loginAndWaitForm(page);

    await page.selectOption('#area', '1');
    await page.click('#btn-codigo');
    await expect(page.locator('#codigo-consecutivo')).toHaveValue('0001');
    await page.fill('#codigo-consecutivo', '0005'); // choca con el existente
    await page.fill('#nombre', 'Colegio San José');
    await page.locator('input[name="clasificacion"][value="INSPECCIONADA"]').check();
    await page.locator('input[name="alcance"][value="exterior"]').check();
    await addFoto(page);
    await page.click('#btn-submit');

    await expect(page.locator('#submit-error')).toHaveText(
      'El código ya existe. Se generó uno nuevo automáticamente; revise y envíe de nuevo.',
    );
    await expect(page.locator('#confirm')).toBeHidden();

    // A diferencia del comportamiento anterior, el formulario NO se limpia:
    // los datos ingresados y la foto se conservan.
    await expect(page.locator('#nombre')).toHaveValue('Colegio San José');
    await expect(page.locator('input[name="clasificacion"][value="INSPECCIONADA"]')).toBeChecked();

    // El área y el botón de generar siguen bloqueados; se ofrece un código
    // nuevo derivado (no el editado que chocó).
    await expect(page.locator('#area')).toBeDisabled();
    await expect(page.locator('#btn-codigo')).toBeDisabled();
    await expect(page.locator('#codigo-display')).toBeVisible();
    await expect(page.locator('#codigo-consecutivo')).toHaveValue('0001');
  });
});

test.describe('Resiliencia: fallo de carga del CDN de Firebase', () => {
  test('si los módulos de Firebase no cargan, la app no se rompe y avisa', async ({ page }) => {
    // No mockFirebase here: abort the gstatic module requests to simulate no CDN.
    await page.route(GSTATIC + '**', (route) => route.abort());
    await page.goto('/');
    // The module graph never executes, so the boot flag is never set.
    await expect(page.locator('#boot-status')).toBeVisible();
    const booted = await page.evaluate(() => window.__atc20Booted);
    expect(booted).toBeFalsy();
    await expect(page.locator('#app')).toBeHidden();
  });
});
