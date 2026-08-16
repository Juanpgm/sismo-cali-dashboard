# Criterios normativos

10 nodos del grafo de conocimiento. Fuente: NSR-10 Título A (cap. A.10) y Guía AIS / Manual de campo para inspección de edificaciones después de un sismo. Cada entrada cita su referencia exacta.

## Alcance de la reparacion segun tipo de dano

Dano solo no estructural se repara por A.9; dano estructural exige evaluacion general y reparar los elementos danados mas los necesarios por resistencia y deriva

> **Fuente:** NSR-10 — A.10.10.1.2, pags 126-127

**Conexiones:**
- → **sugiere** Reforzar / rehabilitar (Acciones)

## Elementos de saturacion

Dano severo en muros de carga, columnas o nudos satura el dano global de la edificacion

> **Fuente:** AIS-manual — Tabla 3-12, pag 59

**Conexiones:**
- ← **indica** Falla en nudos viga-columna (Patologías) (peso 0.85)
- ← **pondera (criticidad)** Muro de carga / portante (Elementos) (peso 1.0)
- ← **pondera (criticidad)** Columna / columneta (Elementos) (peso 1.0)
- ← **pondera (criticidad)** Nudo viga-columna / conexion (Elementos) (peso 1.0)
- ← **pondera (criticidad)** Viga / vigueta (Elementos) (peso 0.8)
- ← **pondera (criticidad)** Losa / entrepiso / contrapiso (Elementos) (peso 0.8)
- ← **pondera (criticidad)** Muro sin calificar (Elementos) (peso 0.7)

## Recomendacion de demolicion requiere experticia

Posible demolicion solo ante riesgo inminente para vecinos o imposibilidad de recuperacion sin dudas, y por evaluador con experiencia en patologia

> **Fuente:** AIS-manual — pags 27/49

**Conexiones:**
- → **sugiere** Demoler la edificacion (Acciones)

## Regla de habitabilidad por combinacion de 4 riesgos

Peligro de colapso (Rojo) con 1+ riesgo muy alto o 2+ riesgos altos; No habitable (Naranja) con 1+ riesgo alto

> **Fuente:** AIS-manual — Tabla 3-22, pag 60

## Reparar vs demoler: factibilidad tecnica

Edificaciones con danos moderados a severos se evaluan para establecer si es tecnicamente factible repararlas; provee criterios para designarlas a demolicion total

> **Fuente:** NSR-10 — A.10.10.1, pag 126

**Conexiones:**
- → **sugiere** Demoler la edificacion (Acciones)

## Resistencia efectiva reducida por estado de conservacion

Resistencia efectiva = existente x phi_c x phi_e; calidad/conservacion mala reduce a 0.6

> **Fuente:** NSR-10 — A.10.4.3.4 Tabla A.10.4-1, pag 121

**Conexiones:**
- ← **indica** Exposicion de barras de refuerzo (Patologías) (peso 0.7)
- ← **indica** Perdida de recubrimiento (Patologías) (peso 0.5)
- ← **indica** Corrosion / oxidacion del refuerzo (Patologías) (peso 0.5)

## Riesgo estructural por severidad y extension del dano

Dano severo en >15% de elementos verticales o >20% de horizontales: riesgo muy alto (peligro de colapso); severo 5-15% o fuerte 10-30%: riesgo alto (no habitable)

> **Fuente:** AIS-manual — Tabla 3-13, pags 49-50

**Conexiones:**
- → **sugiere** Estudio de vulnerabilidad (Acciones)
- → **sugiere** Apuntalar (Acciones)
- ← **indica** Aplastamiento del concreto/mamposteria (Patologías) (peso 0.9)
- ← **indica** Pandeo de barras/elementos (Patologías) (peso 0.9)
- ← **indica** Grietas diagonales (falla por cortante) (Patologías) (peso 0.7)
- ← **indica** Grietas horizontales por flexion (Patologías) (peso 0.5)
- ← **indica** Fisuracion leve (Patologías) (peso 0.25)
- ← **indica** Agrietamiento sin orientacion declarada (Patologías) (peso 0.45)
- ← **indica** Dislocacion de piezas de mamposteria (Patologías) (peso 0.7)
- ← **indica** Punzonamiento en losas (Patologías) (peso 0.8)

## Riesgo geotecnico

Fallas severas de cimentacion, hundimiento o inclinacion: riesgo muy alto

> **Fuente:** AIS-manual — Tabla 3-3, pag 32

**Conexiones:**
- ← **indica** Asentamiento / hundimiento diferencial (Patologías) (peso 0.8)
- ← **indica** Deslizamiento / falla de talud / socavacion (Patologías) (peso 0.8)
- ← **pondera (criticidad)** Cimentacion (Elementos) (peso 0.9)

## Riesgo no estructural

Danos severos generalizados o elementos en peligro de caer: riesgo alto; no genera colapso por si solo

> **Fuente:** AIS-manual — Tabla 3-21, pags 59-60

**Conexiones:**
- ← **indica** Desprendimiento de acabados/elementos (Patologías) (peso 0.5)
- ← **pondera (criticidad)** Cubierta / estructura de techo (Elementos) (peso 0.3)
- ← **pondera (criticidad)** Elemento no estructural (divisorios, acabados, cielos rasos) (Elementos) (peso 0.3)

## Riesgo por estabilidad global

Colapso total o parcial >50% o inclinacion notable: riesgo muy alto; colapso parcial 5-50%: alto

> **Fuente:** AIS-manual — Tabla 3-2, pags 27-28

**Conexiones:**
- → **sugiere** Demoler la edificacion (Acciones)
- ← **indica** Desplome / inclinacion permanente (Patologías) (peso 0.9)
