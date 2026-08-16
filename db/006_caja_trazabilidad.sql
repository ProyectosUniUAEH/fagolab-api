-- 006_caja_trazabilidad.sql
-- Trazabilidad editable de la caja Petri (quién/cuándo: registrada y estado) como JSONB.
ALTER TABLE cajas_petri ADD COLUMN IF NOT EXISTS trazabilidad JSONB;
