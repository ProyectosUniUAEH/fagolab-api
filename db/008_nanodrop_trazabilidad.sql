-- 008_nanodrop_trazabilidad.sql
-- Trazabilidad editable de una lectura NanoDrop: permite registrar manualmente
-- quién/qué día/qué hora de cada evento (lectura, muestra procesada, recepción).
-- JSONB con forma { <evento>: { usuario, fecha, hora } }.
ALTER TABLE lecturas_nanodrop ADD COLUMN IF NOT EXISTS trazabilidad JSONB;
