-- 025_fichas.sql — la ficha científica: la única salida generada por el modelo.
--
-- Todo lo anterior del pipeline son hechos (QC, BLAST, taxonomía, literatura). Aquí nace
-- texto que no existía. Por eso cada ficha guarda **cómo** se generó: modelo, temperatura,
-- seed y la huella exacta de la evidencia que se le pasó al modelo.
--
-- Ese registro no es burocracia: es lo que permite (a) reproducir una ficha, (b) demostrar
-- que la interpretación se apoya en evidencia concreta y no en memoria del modelo, y
-- (c) el experimento de variar una sola variable y comparar resultados.

CREATE TABLE IF NOT EXISTS fichas_analisis (
  id_ficha UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  id_secuenciacion UUID NOT NULL REFERENCES secuenciaciones(id_secuenciacion) ON DELETE CASCADE,

  -- Salida del modelo: texto completo y sus secciones ya separadas.
  texto TEXT NOT NULL,
  secciones JSONB NOT NULL DEFAULT '{}'::jsonb,

  -- Cómo se generó. Sin esto una ficha no es reproducible ni comparable.
  proveedor TEXT,
  modelo TEXT NOT NULL,
  temperatura NUMERIC(4,2),
  top_p NUMERIC(4,2),
  seed BIGINT,
  prompt_sistema TEXT,

  -- Con qué se generó. `con_evidencia = false` es el control del experimento: la misma
  -- pregunta sin evidencia, para mostrar en qué se nota el grounding.
  con_evidencia BOOLEAN NOT NULL DEFAULT TRUE,
  evidencia_sha256 TEXT,
  evidencia_resumen JSONB NOT NULL DEFAULT '{}'::jsonb,

  tokens_entrada INTEGER,
  tokens_salida INTEGER,
  duracion_ms INTEGER,
  etiqueta_experimento TEXT,
  generada_por TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE fichas_analisis IS
  'Interpretación científica generada por IA. Se conserva junto con sus parámetros y el hash de la evidencia para poder reproducirla y compararla.';
COMMENT ON COLUMN fichas_analisis.con_evidencia IS
  'FALSE genera la ficha a ciegas, sin BLAST ni literatura. Es el control que evidencia la alucinación.';
COMMENT ON COLUMN fichas_analisis.etiqueta_experimento IS
  'Nombre corto de la corrida experimental, para agrupar fichas que varían una sola variable.';

CREATE INDEX IF NOT EXISTS idx_fichas_secuenciacion
  ON fichas_analisis(id_secuenciacion, created_at DESC);
