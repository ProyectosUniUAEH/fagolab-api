-- 005_subcultivo_trazabilidad.sql
-- Guarda la trazabilidad editable del subcultivo (quién/cuándo en cada paso:
-- creado, evaluación de pureza, apto para extracción) como JSONB.
ALTER TABLE subcultivos_petri ADD COLUMN IF NOT EXISTS trazabilidad JSONB;
