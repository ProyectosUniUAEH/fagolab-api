-- 024_analisis.sql — corridas de análisis con estado, para trabajo que tarda minutos.
--
-- Por qué: BLAST contra NCBI no responde en un request HTTP. Se envía la consulta, NCBI
-- devuelve un identificador (RID) y hay que consultarlo cada pocos segundos hasta que
-- termina. Eso obliga a modelar la corrida como un objeto con estado propio, que la UI
-- consulta, y no como una llamada síncrona.
--
-- `corridas_analisis` ya existía en el esquema base pero era genérica y no se usaba.
-- Aquí se le da lo que faltaba: a qué secuenciación pertenece, qué herramienta la
-- produjo, en qué va, y dónde quedó el resultado.

ALTER TABLE corridas_analisis
  ADD COLUMN IF NOT EXISTS id_secuenciacion     UUID REFERENCES secuenciaciones(id_secuenciacion) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS id_archivo_secuencia UUID REFERENCES archivos_secuencia(id_archivo_secuencia) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS herramienta          TEXT,
  ADD COLUMN IF NOT EXISTS referencia_externa   TEXT,
  ADD COLUMN IF NOT EXISTS progreso             TEXT,
  ADD COLUMN IF NOT EXISTS resultado            JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS error                TEXT;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'corridas_analisis_estado_chk') THEN
    ALTER TABLE corridas_analisis ADD CONSTRAINT corridas_analisis_estado_chk
      CHECK (estado_corrida IN ('registrada','en_curso','completada','fallida','cancelada'));
  END IF;
END $$;

COMMENT ON COLUMN corridas_analisis.referencia_externa IS
  'Identificador del trabajo en el servicio externo (por ejemplo el RID que devuelve NCBI BLAST).';
COMMENT ON COLUMN corridas_analisis.progreso IS
  'Texto corto y legible del paso actual, para que la científica sepa qué está pasando.';

CREATE INDEX IF NOT EXISTS idx_corridas_secuenciacion
  ON corridas_analisis(id_secuenciacion, created_at DESC);

-- Los hits de BLAST se guardan en `resultados_blast`, que ya existe con la forma correcta.
-- Solo faltaba poder rehacer una corrida sin chocar con la anterior: el ranking es único
-- por secuenciación, así que una corrida nueva reemplaza a la previa.
CREATE INDEX IF NOT EXISTS idx_resultados_blast_corrida ON resultados_blast(corrida_blast);
