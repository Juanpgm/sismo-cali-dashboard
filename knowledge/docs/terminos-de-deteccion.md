# Términos de detección

30 nodos del grafo de conocimiento. Fuente: NSR-10 Título A (cap. A.10) y Guía AIS / Manual de campo para inspección de edificaciones después de un sismo. Cada entrada cita su referencia exacta.

## acero expuesto

> **Fuente:** AIS-manual — Tabla 3-5, pags 44-45

**Patrón de detección:** `(?:acero|refuerzo|barras?)\s+(?:[#\w.]+\s+){0,2}expuest|exposici.n\s+de\s+(?:barras|acero|refuerzo)|acero\s+(?:a\s+la\s+)?vista`

**Conexiones:**
- → **detecta** Exposicion de barras de refuerzo (Patologías)

## aplastamiento

> **Fuente:** AIS-manual — Tabla 3-5, pags 44-45

**Patrón de detección:** `aplasta`

**Conexiones:**
- → **detecta** Aplastamiento del concreto/mamposteria (Patologías)

## apuntalar

> **Fuente:** AIS-manual — Seccion 9, pag 12

**Patrón de detección:** `apuntal`

**Conexiones:**
- → **detecta** Apuntalar (Acciones)

## asentamiento

> **Fuente:** AIS-manual — pags 38-39

**Patrón de detección:** `asentamiento|asentad|hundimiento|hundid|subsidencia`

**Conexiones:**
- → **detecta** Asentamiento / hundimiento diferencial (Patologías)

## cimentacion

> **Fuente:** AIS-manual — pags 38-39

**Patrón de detección:** `cimentaci|cimientos?|zapatas?|fundaci.n`

**Conexiones:**
- → **detecta** Cimentacion (Elementos)

## columna

> **Fuente:** AIS-manual — Tabla 3-4, pag 43

**Patrón de detección:** `columnas?|columnetas?`

**Conexiones:**
- → **detecta** Columna / columneta (Elementos)

## corrosion

> **Fuente:** NSR-10 — Tabla A.10.4-1, pag 121

**Patrón de detección:** `corro.i.n|corro.d|oxid`

**Conexiones:**
- → **detecta** Corrosion / oxidacion del refuerzo (Patologías)

## cubierta

> **Fuente:** AIS-manual — Tabla 3-17, pag 55

**Patrón de detección:** `cubiertas?|tejas?|techos?|cerchas?|correas?|culatas?`

**Conexiones:**
- → **detecta** Cubierta / estructura de techo (Elementos)

## demoler

> **Fuente:** AIS-manual — Seccion 9, pags 12/27

**Patrón de detección:** `demol|derrib|desmont`

**Conexiones:**
- → **detecta** Demoler la edificacion (Acciones)

## deslizamiento/talud

> **Fuente:** AIS-manual — Tabla 3-3, pag 32

**Patrón de detección:** `socav|deslizamiento|talud|remoci.n\s+en\s+masa|ladera|licuaci.n`

**Conexiones:**
- → **detecta** Deslizamiento / falla de talud / socavacion (Patologías)

## desplome/inclinacion

> **Fuente:** AIS-manual — Tabla 3-2, pags 27-28

**Patrón de detección:** `desplom|inclinaci|inclinad|volcam|desviaci.n|fuera\s+de\s+plomo|ladead`

**Conexiones:**
- → **detecta** Desplome / inclinacion permanente (Patologías)

## desprendimiento

> **Fuente:** AIS-manual — Tabla 3-21, pag 59

**Patrón de detección:** `desprend|peligro\s+de\s+caer|riesgo\s+de\s+ca.da`

**Conexiones:**
- → **detecta** Desprendimiento de acabados/elementos (Patologías)

## dislocacion

> **Fuente:** AIS-manual — Tabla 3-6, pag 47

**Patrón de detección:** `dislocaci|dislocad|desencaj`

**Conexiones:**
- → **detecta** Dislocacion de piezas de mamposteria (Patologías)

## estudio de vulnerabilidad

> **Fuente:** AIS-manual — pag 49

**Patrón de detección:** `vulnerabilidad`

**Conexiones:**
- → **detecta** Estudio de vulnerabilidad (Acciones)

## evacuar

> **Fuente:** AIS-manual — Seccion 9, pag 12

**Patrón de detección:** `evacuaci|evacuar|desaloj`

**Conexiones:**
- → **detecta** Evacuar (Acciones)

## falla de nudo

> **Fuente:** AIS-manual — pag 44

**Patrón de detección:** `falla\w*\s+(?:de\s+|en\s+)?(?:los\s+)?nudos?|nudos?\s+(?:fallad|agrietad|da.ad)|uni.n\s+viga[\s-]columna`

**Conexiones:**
- → **detecta** Falla en nudos viga-columna (Patologías)

## fisura

> **Fuente:** AIS-manual — Tabla 3-5, pags 44-45

**Patrón de detección:** `fisur`

**Conexiones:**
- → **detecta** Fisuracion leve (Patologías)

## grieta

> **Fuente:** AIS-manual — Tabla 3-5, pags 44-45

**Patrón de detección:** `griet|agrieta`

**Conexiones:**
- → **detecta** Agrietamiento sin orientacion declarada (Patologías)

## grieta diagonal

> **Fuente:** AIS-manual — pags 44-47

**Patrón de detección:** `griet\w+\s+(?:\w+\s+){0,2}diagonal|agrietamiento\s+diagonal|fisur\w+\s+diagonal|griet\w+\s+en\s+x\b|falla\s+(?:de|por)\s+cortante`

**Conexiones:**
- → **detecta** Grietas diagonales (falla por cortante) (Patologías)

## grieta horizontal

> **Fuente:** AIS-manual — pags 46-47

**Patrón de detección:** `griet\w+\s+horizontal|agrietamiento\s+horizontal`

**Conexiones:**
- → **detecta** Grietas horizontales por flexion (Patologías)

## losa/entrepiso

> **Fuente:** AIS-manual — Tabla 3-11, pag 58

**Patrón de detección:** `losas?\b|entrepisos?|contrapisos?|placas?\s+de\s+(?:piso|entrepiso|concreto)`

**Conexiones:**
- → **detecta** Losa / entrepiso / contrapiso (Elementos)

## muro

> **Fuente:** AIS-manual — pags 51/53

**Patrón de detección:** `muros?\b|mamposter`

**Conexiones:**
- → **detecta** Muro sin calificar (Elementos)

## muro de carga

> **Fuente:** AIS-manual — Tabla 3-4, pag 43

**Patrón de detección:** `muros?\s+(?:de\s+carga|portantes?|estructurales?|de\s+contenci.n)|mamposter.a\s+(?:estructural|portante|de\s+carga)|tapia|adobe|bahareque`

**Conexiones:**
- → **detecta** Muro de carga / portante (Elementos)

## no estructural

> **Fuente:** AIS-manual — Tabla 3-21, pags 59-61

**Patrón de detección:** `pa.etes?|divisori|particion|acabados?|enchapes?|cielos?\s+rasos?|antepechos?|fachadas?`

**Conexiones:**
- → **detecta** Elemento no estructural (divisorios, acabados, cielos rasos) (Elementos)

## nudo

> **Fuente:** AIS-manual — Tabla 3-12, pag 59

**Patrón de detección:** `nudos?\b|conexi.n\s+viga`

**Conexiones:**
- → **detecta** Nudo viga-columna / conexion (Elementos)

## pandeo

> **Fuente:** AIS-manual — Tabla 3-5, pags 44-45

**Patrón de detección:** `pande[oa]`

**Conexiones:**
- → **detecta** Pandeo de barras/elementos (Patologías)

## perdida de recubrimiento

> **Fuente:** AIS-manual — Tabla 3-5, pags 44-45

**Patrón de detección:** `p.rdida\s+de(?:l)?\s+(?:recubrimiento|revestimiento)|(?:recubrimiento|revestimiento)\s+(?:perdid|desprendid|ca.d)|descascaramiento`

**Conexiones:**
- → **detecta** Perdida de recubrimiento (Patologías)

## punzonamiento

> **Fuente:** AIS-manual — pag 44

**Patrón de detección:** `punzonamiento`

**Conexiones:**
- → **detecta** Punzonamiento en losas (Patologías)

## reforzar

> **Fuente:** NSR-10 — A.10.9.1, pag 124

**Patrón de detección:** `reforz|rehabilitaci.n\s+s.smica`

**Conexiones:**
- → **detecta** Reforzar / rehabilitar (Acciones)

## viga

> **Fuente:** AIS-manual — Tabla 3-4, pag 43

**Patrón de detección:** `vigas?\b|viguetas?|dintel`

**Conexiones:**
- → **detecta** Viga / vigueta (Elementos)
