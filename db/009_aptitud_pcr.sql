-- 009_aptitud_pcr.sql
-- Módulo de apoyo a la decisión: aptitud de una muestra de ADN (vial) para PCR.
-- Motor de reglas configurable + registro de evaluación, decisión humana y
-- resultado real de PCR, dejando el esquema listo para un clasificador futuro.
-- No duplica datos: la trazabilidad se conserva por el vial (lectura ← vial → PCR).

-- --- Reglas configurables (criterios del protocolo, editables; no verdades universales) ---
CREATE TABLE IF NOT EXISTS reglas_aptitud (
  id_regla UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  parametro TEXT NOT NULL,                 -- 'ratio_260_280' | 'ratio_260_230' | 'concentracion_ng_ul'
  etiqueta TEXT NOT NULL,
  minimo NUMERIC,                          -- límite inferior aceptable (NULL = sin mínimo)
  maximo NUMERIC,                          -- límite superior aceptable (NULL = sin máximo)
  unidad TEXT,
  severidad TEXT NOT NULL DEFAULT 'media', -- 'alta' | 'media' | 'baja'
  activo BOOLEAN NOT NULL DEFAULT true,
  version INTEGER NOT NULL DEFAULT 1,
  nota TEXT,                               -- p. ej. "Criterio inicial del protocolo"
  definido_por TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (parametro)
);

-- --- Evaluación producida por el motor (reglas ahora; modelo en el futuro) ---
CREATE TABLE IF NOT EXISTS evaluaciones_aptitud (
  id_evaluacion UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_lectura_nanodrop UUID NOT NULL REFERENCES lecturas_nanodrop(id_lectura_nanodrop) ON DELETE CASCADE,
  motor TEXT NOT NULL DEFAULT 'reglas',    -- 'reglas' | 'modelo'
  version_motor TEXT,
  recomendacion TEXT NOT NULL,             -- apta_pcr | revision_recomendada | purificar | diluir | repetir_extraccion | datos_insuficientes | revision_humana
  nivel_alerta TEXT NOT NULL,              -- ok | aviso | critico
  nivel_confianza TEXT NOT NULL,           -- alta | media | baja
  factores JSONB,                          -- { razones:[], fueraRango:[], advertencias:[], valores:{} }
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_eval_lectura ON evaluaciones_aptitud(id_lectura_nanodrop);

-- --- Decisión de la investigadora (human-in-the-loop), separada de la del sistema ---
CREATE TABLE IF NOT EXISTS decisiones_aptitud (
  id_decision UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_evaluacion UUID NOT NULL REFERENCES evaluaciones_aptitud(id_evaluacion) ON DELETE CASCADE,
  recomendacion_sistema TEXT NOT NULL,     -- copia de lo que sugirió el sistema
  decision_final TEXT NOT NULL,            -- lo que decidió la investigadora
  acepto_recomendacion BOOLEAN NOT NULL,
  motivo_cambio TEXT,                      -- obligatorio cuando no acepta
  usuario TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dec_eval ON decisiones_aptitud(id_evaluacion);

-- --- Versiones del modelo supervisado (andamiaje; sin entrenar nada real todavía) ---
CREATE TABLE IF NOT EXISTS modelo_versiones (
  id_modelo UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  version TEXT NOT NULL,
  estado TEXT NOT NULL DEFAULT 'datos_insuficientes', -- datos_insuficientes | en_entrenamiento | activo | archivado
  n_muestras_entrenamiento INTEGER NOT NULL DEFAULT 0,
  metricas JSONB,
  nota TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --- Contexto de la lectura y cierre de ciclo con el resultado real de PCR ---
ALTER TABLE lecturas_nanodrop ADD COLUMN IF NOT EXISTS tipo_muestra TEXT;
ALTER TABLE lecturas_nanodrop ADD COLUMN IF NOT EXISTS metodo_extraccion TEXT;
ALTER TABLE lecturas_nanodrop ADD COLUMN IF NOT EXISTS dilucion TEXT;

ALTER TABLE pcr_reacciones ADD COLUMN IF NOT EXISTS resultado_detallado TEXT; -- amplificacion_correcta | debil | inespecifica | sin_amplificacion | no_concluyente
ALTER TABLE pcr_reacciones ADD COLUMN IF NOT EXISTS causa_fallo TEXT;
ALTER TABLE pcr_reacciones ADD COLUMN IF NOT EXISTS id_lectura_nanodrop UUID REFERENCES lecturas_nanodrop(id_lectura_nanodrop);

-- --- Semilla de reglas: criterios iniciales del protocolo del laboratorio (editables) ---
INSERT INTO reglas_aptitud (parametro, etiqueta, minimo, maximo, unidad, severidad, nota, definido_por) VALUES
  ('ratio_260_280', 'Relación 260/280', 1.8, 2.0, '', 'alta',
   'Criterio inicial del protocolo del laboratorio', 'protocolo'),
  ('ratio_260_230', 'Relación 260/230', 2.0, 2.2, '', 'media',
   'Criterio inicial del protocolo del laboratorio', 'protocolo'),
  ('concentracion_ng_ul', 'Concentración de ADN', 10, 500, 'ng/µL', 'media',
   'Criterio inicial del protocolo del laboratorio', 'protocolo')
ON CONFLICT (parametro) DO NOTHING;

-- Registro inicial del modelo: aún sin datos suficientes para entrenar.
INSERT INTO modelo_versiones (version, estado, nota)
VALUES ('v0', 'datos_insuficientes', 'Sin experimentos con resultado real suficientes para entrenar.')
ON CONFLICT DO NOTHING;
